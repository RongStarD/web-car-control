from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from .config import feature_components
from .models import Phase, RuntimeState, Settings
from .supervisor import Supervisor

StateCallback = Callable[[Dict[str, Any]], Awaitable[None]]
TransitionCallback = Callable[[Optional[str], str], Awaitable[None]]


class SystemOrchestrator:
    def __init__(
        self,
        settings: Settings,
        supervisor: Supervisor,
        on_state: StateCallback,
        before_transition: TransitionCallback | None = None,
        after_transition: TransitionCallback | None = None,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.on_state = on_state
        self.before_transition = before_transition
        self.after_transition = after_transition
        self.state = RuntimeState(changed_at=time.time())
        self._lock: asyncio.Lock | None = None

    def _runtime_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _publish(self) -> None:
        await self.on_state({"type": "status", "runtime": self.state.as_dict()})

    async def _set_phase(self, phase: Phase, message: str = "") -> None:
        self.state.phase = phase
        self.state.message = message
        self.state.changed_at = time.time()
        await self._publish()

    def desired_components(self, feature: str) -> list[str]:
        return feature_components(self.settings, feature)

    def feature_target(self, feature: str) -> str | None:
        components = self.desired_components(feature)
        targets = {self.settings.components[name].target for name in components}
        if not targets:
            return None
        if len(targets) != 1:
            raise RuntimeError(f"Feature {feature} spans multiple runtime targets: {sorted(targets)}")
        return next(iter(targets))

    async def recover(self) -> RuntimeState:
        async with self._runtime_lock():
            running: list[str] = []
            for target in self.settings.targets:
                target_state = await self.supervisor.target_status(target)
                if not target_state.running:
                    continue
                for component in self.settings.components:
                    if self.settings.components[component].target != target:
                        continue
                    state = await self.supervisor.status(component)
                    if state.running:
                        running.append(component)

            self.state.active_components = running
            if not running:
                self.state.feature = "IDLE"
                self.state.phase = Phase.IDLE
                self.state.message = ""
            else:
                matches = [
                    feature.name
                    for feature in self.settings.features.values()
                    if feature.enabled and set(self.desired_components(feature.name)) == set(running)
                ]
                if len(matches) == 1:
                    self.state.feature = matches[0]
                    self.state.phase = Phase.ERROR
                    self.state.message = "Recovered processes are locked; operator confirmation required"
                else:
                    self.state.feature = "IDLE"
                    self.state.phase = Phase.ERROR
                    self.state.message = "Orphaned managed processes require operator review"
            self.state.changed_at = time.time()
            await self._publish()
            return self.state

    async def _ensure_lifecycle_active(self, component: str) -> None:
        config = self.settings.components[component]
        if not config.lifecycle_node:
            return

        deadline = time.monotonic() + self.settings.runtime.readiness_grace_seconds
        last_state = "unavailable"
        while time.monotonic() < deadline:
            last_state = await self.supervisor.lifecycle(config.target, config.lifecycle_node)
            if last_state == "active":
                return
            try:
                if last_state == "unconfigured":
                    await self.supervisor.lifecycle_set(config.target, config.lifecycle_node, "configure")
                elif last_state == "inactive":
                    await self.supervisor.lifecycle_set(config.target, config.lifecycle_node, "activate")
            except RuntimeError:
                pass
            await asyncio.sleep(0.35)
        raise RuntimeError(f"Lifecycle node {config.lifecycle_node} did not become active ({last_state})")

    async def set_feature(self, feature_name: str) -> RuntimeState:
        feature_name = feature_name.upper()
        if feature_name not in self.settings.features:
            raise ValueError(f"Unknown feature: {feature_name}")
        feature = self.settings.features[feature_name]
        if not feature.enabled:
            raise ValueError(feature.blocked_reason or f"Feature {feature_name} is disabled")
        if feature_name == "IDLE":
            return await self.stop_all()

        async with self._runtime_lock():
            if self.state.feature == feature_name and self.state.phase in {Phase.READY, Phase.DEGRADED}:
                return self.state

            previous_feature = self.state.feature
            previous_phase = self.state.phase
            previous_components = list(self.state.active_components)
            previous_target = self.feature_target(previous_feature) if previous_feature != "IDLE" else None
            target = self.feature_target(feature_name)
            desired = self.desired_components(feature_name)
            desired_set = set(desired)
            active_set = set(self.state.active_components)
            started_now: list[str] = []

            self.state.generation += 1
            self.state.feature = feature_name
            await self._set_phase(Phase.STARTING, f"Starting {feature.label}")

            try:
                if self.before_transition:
                    await self.before_transition(previous_target, target or "switch")

                for component in reversed(self.state.active_components):
                    if component not in desired_set:
                        await self.supervisor.stop(component)
                        active_set.discard(component)

                if previous_target and previous_target != target and self.settings.runtime.stop_inactive_containers:
                    await self.supervisor.stop_target(previous_target)

                if target:
                    if self.settings.runtime.stop_inactive_containers:
                        for other_target in self.settings.targets:
                            if other_target != target:
                                await self.supervisor.stop_target(other_target)
                    await self.supervisor.ensure_target(target)

                for component in desired:
                    if component not in active_set:
                        await self.supervisor.start(component)
                        active_set.add(component)
                        started_now.append(component)
                    await self._ensure_lifecycle_active(component)

                self.state.active_components = desired
                if self.after_transition:
                    await self.after_transition(target, feature.control_source)
                await asyncio.sleep(self.settings.runtime.start_settle_seconds)
                await self._set_phase(Phase.READY, f"{feature.label} ready")
                return self.state
            except Exception as exc:
                for component in reversed(started_now):
                    try:
                        await self.supervisor.stop(component)
                    except Exception:
                        pass
                    active_set.discard(component)

                rollback_error: Exception | None = None
                if previous_feature != "IDLE" and previous_components:
                    try:
                        if target and target != previous_target and self.settings.runtime.stop_inactive_containers:
                            await self.supervisor.stop_target(target)
                            active_set = {
                                name
                                for name in active_set
                                if self.settings.components[name].target != target
                            }
                        if previous_target:
                            await self.supervisor.ensure_target(previous_target)
                        for component in previous_components:
                            if component not in active_set:
                                await self.supervisor.start(component)
                                active_set.add(component)
                            await self._ensure_lifecycle_active(component)
                        self.state.feature = previous_feature
                        self.state.active_components = previous_components
                        if self.after_transition and previous_target:
                            source = self.settings.features[previous_feature].control_source
                            await self.after_transition(previous_target, source)
                        restored_phase = (
                            previous_phase
                            if previous_phase in {Phase.READY, Phase.DEGRADED}
                            else Phase.ERROR
                        )
                        await self._set_phase(
                            restored_phase,
                            f"Switch failed; restored {self.settings.features[previous_feature].label}",
                        )
                    except Exception as restore_exc:
                        rollback_error = restore_exc

                if rollback_error is not None or previous_feature == "IDLE" or not previous_components:
                    self.state.active_components = [
                        name for name in self.state.active_components if name in active_set
                    ]
                    await self._set_phase(
                        Phase.ERROR,
                        f"{exc}; rollback failed: {rollback_error}" if rollback_error else str(exc),
                    )
                if rollback_error:
                    raise RuntimeError(f"{exc}; rollback failed: {rollback_error}") from exc
                raise RuntimeError(f"{exc}; previous feature restored") from exc

    async def stop_all(self) -> RuntimeState:
        async with self._runtime_lock():
            previous_feature = self.state.feature
            previous_target = self.feature_target(previous_feature) if previous_feature != "IDLE" else None
            if previous_target is None and self.state.active_components:
                active_targets = {
                    self.settings.components[name].target
                    for name in self.state.active_components
                }
                if len(active_targets) == 1:
                    previous_target = next(iter(active_targets))
            self.state.generation += 1
            await self._set_phase(Phase.STOPPING, "Stopping managed processes")
            errors: list[str] = []

            if self.before_transition:
                try:
                    await self.before_transition(previous_target, "stop")
                except Exception as exc:
                    errors.append(f"safety: {exc}")

            for component in reversed(self.state.active_components):
                try:
                    await self.supervisor.stop(component)
                except Exception as exc:
                    errors.append(f"{component}: {exc}")

            if previous_target and self.settings.runtime.stop_inactive_containers:
                try:
                    await self.supervisor.stop_target(previous_target)
                except Exception as exc:
                    errors.append(f"target: {exc}")

            self.state.feature = "IDLE"
            self.state.active_components = []
            if errors:
                await self._set_phase(Phase.ERROR, "; ".join(errors))
            else:
                await self._set_phase(Phase.IDLE, "System idle")
            return self.state

    async def mark_degraded(self, message: str) -> None:
        if self.state.phase == Phase.READY:
            await self._set_phase(Phase.DEGRADED, message)

    async def mark_ready(self) -> None:
        if self.state.phase == Phase.DEGRADED:
            label = self.settings.features[self.state.feature].label
            await self._set_phase(Phase.READY, f"{label} ready")

    async def mark_error(self, message: str) -> None:
        if self.state.phase not in {Phase.IDLE, Phase.STOPPING, Phase.ERROR}:
            await self._set_phase(Phase.ERROR, message)
