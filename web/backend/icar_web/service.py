from __future__ import annotations

import asyncio
import re
import shlex
from typing import Any, Awaitable, Callable

from .bridge import Bridge
from .config import feature_components
from .events import EventHub
from .health import HealthMonitor
from .manager import SystemOrchestrator
from .map_store import MapStore
from .models import HealthLevel, Phase, Settings
from .supervisor import Supervisor

MAP_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ControlService:
    def __init__(
        self,
        settings: Settings,
        supervisor: Supervisor,
        bridge: Bridge,
        events: EventHub,
        demo: bool = False,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.bridge = bridge
        self.events = events
        self.demo = demo
        self.health = HealthMonitor(settings, supervisor)
        self.maps = MapStore(settings.map_save)
        self.manager = SystemOrchestrator(
            settings,
            supervisor,
            events.publish,
            before_transition=self._before_transition,
            after_transition=self._after_transition,
        )
        self._health_task: asyncio.Task[None] | None = None
        self._last_health: dict[str, Any] | None = None
        self._warn_count = 0
        self._ok_count = 0
        self._clients = 0
        self._operation_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.manager.recover()
        if self.manager.state.active_components:
            active_targets = {
                self.settings.components[name].target
                for name in self.manager.state.active_components
            }
            target = next(iter(active_targets)) if len(active_targets) == 1 else None
            if target:
                await self.bridge.start(target)
                await self.bridge.send({"type": "set_control_mode", "source": "IDLE"})
                await self.bridge.send({"type": "emergency_stop"})
        await self._inspect_health_once()
        self._health_task = asyncio.create_task(self._health_loop())

    async def close(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        await self.bridge.stop()

    async def _run_operation(self, label: str, operation: Callable[[], Awaitable[Any]]) -> None:
        try:
            await operation()
        except Exception as exc:
            await self.events.publish(
                {"type": "log", "level": "ERROR", "message": f"{label}: {exc}"}
            )
        finally:
            self._operation_task = None

    async def _schedule_operation(
        self,
        label: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> dict[str, Any]:
        if self._operation_task and not self._operation_task.done():
            raise RuntimeError("A system transition is already in progress")
        self._operation_task = asyncio.create_task(self._run_operation(label, operation))
        await asyncio.sleep(0)
        return self.manager.state.as_dict()

    async def _before_transition(self, previous_target: str | None, next_target: str) -> None:
        if self.bridge.available:
            try:
                await self.bridge.send({"type": "emergency_stop"})
                await asyncio.sleep(0.12)
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                await self.bridge.stop()
                return
            if next_target != previous_target:
                await self.bridge.stop()

    async def _after_transition(self, target: str | None, control_source: str) -> None:
        if target is None:
            return
        await self.bridge.start(target)
        await self.bridge.send({"type": "set_control_mode", "source": control_source})
        await self.bridge.send({"type": "clear_emergency"})

    async def _health_loop(self) -> None:
        while True:
            try:
                await self._inspect_health_once()
            except Exception as exc:
                await self.events.publish({"type": "log", "level": "ERROR", "message": f"Health monitor: {exc}"})
            await asyncio.sleep(self.settings.runtime.health_interval_seconds)

    async def _inspect_health_once(self) -> None:
        snapshot = await self.health.inspect(
            self.manager.state.active_components,
            self.bridge.topic_freshness() if self.bridge.available else {},
        )
        self._last_health = snapshot.as_dict()
        await self.events.publish({"type": "health", **self._last_health})

        if self.manager.state.phase in {Phase.STARTING, Phase.STOPPING}:
            self._warn_count = 0
            self._ok_count = 0
            return

        if snapshot.overall == HealthLevel.ERROR:
            self._warn_count += 1
            self._ok_count = 0
            if self._warn_count >= 2 and self.manager.state.phase != Phase.ERROR:
                if self.bridge.available:
                    await self.bridge.send({"type": "emergency_stop"})
                await self.manager.mark_error(snapshot.warnings[0] if snapshot.warnings else "Runtime failure")
        elif snapshot.overall == HealthLevel.WARN:
            self._warn_count += 1
            self._ok_count = 0
            if self._warn_count >= 2:
                await self.manager.mark_degraded(snapshot.warnings[0])
        else:
            self._ok_count += 1
            self._warn_count = 0
            if self._ok_count >= 2:
                await self.manager.mark_ready()

    def bootstrap(self) -> dict[str, Any]:
        features = []
        for feature in self.settings.features.values():
            item = feature.public_dict()
            components = feature_components(self.settings, feature.name)
            item["components"] = [
                {
                    "id": name,
                    "target": self.settings.components[name].target,
                    "nodes": list(self.settings.components[name].nodes),
                    "topics": list(self.settings.components[name].topics),
                    "lifecycle_node": self.settings.components[name].lifecycle_node,
                }
                for name in components
            ]
            features.append(item)
        return {
            "environment": "demo" if self.demo else "live",
            "features": features,
            "runtime": self.manager.state.as_dict(),
            "bridge": self.bridge.summary(),
            "health": self._last_health,
            "latest": self.events.latest,
            "maps": self.maps.list_profiles(),
            "active_map": self.maps.active_name(),
        }

    def preflight(self) -> dict[str, Any]:
        return {
            "environment": "demo" if self.demo else "live",
            "runtime": self.manager.state.as_dict(),
            "bridge": self.bridge.summary(),
            "health": self._last_health,
        }

    async def set_feature(self, feature: str) -> dict[str, Any]:
        feature = feature.upper()
        if feature not in self.settings.features:
            raise ValueError(f"Unknown feature: {feature}")
        config = self.settings.features[feature]
        if not config.enabled:
            raise ValueError(config.blocked_reason or f"Feature {feature} is disabled")
        return await self._schedule_operation(
            f"Failed to start {config.label}",
            lambda: self.manager.set_feature(feature),
        )

    async def stop(self) -> dict[str, Any]:
        return await self._schedule_operation("Failed to stop system", self.manager.stop_all)

    async def command(self, payload: dict[str, Any]) -> None:
        command_type = str(payload.get("type", ""))
        allowed = {
            "drive",
            "goal",
            "initial_pose",
            "cancel_goal",
            "emergency_stop",
            "clear_emergency",
            "map_initial_pose",
            "route_start",
        }
        if command_type not in allowed:
            raise ValueError(f"Unsupported command: {command_type}")
        feature = self.manager.state.feature
        if command_type == "drive" and feature not in {"WEB_MANUAL", "SLAM"}:
            raise ValueError("Drive commands are only allowed in manual and mapping features")
        if command_type in {"initial_pose", "cancel_goal", "map_initial_pose"} and feature not in {"NAV_DWA", "NAV_TEB", "TASK_ROUTE"}:
            raise ValueError("Navigation commands require an active navigation feature")
        if command_type == "goal" and feature not in {"NAV_DWA", "NAV_TEB"}:
            raise ValueError("Goal commands require an active navigation feature")
        if command_type == "goal" and not bool(
            self.events.latest.get("pose", {}).get("localized", False)
        ):
            raise ValueError("Set the initial pose before sending a navigation goal")
        if command_type == "map_initial_pose":
            profile = self._active_profile(str(payload.get("map_name", "")))
            default_id = profile.get("default_pose_id")
            point = next((item for item in profile["waypoints"] if item["id"] == default_id), None)
            if point is None:
                raise ValueError("The selected map has no default pose")
            await self.bridge.send({"type": "initial_pose", **point})
            return
        if command_type == "route_start":
            if feature != "TASK_ROUTE":
                raise ValueError("Route commands require the task feature")
            if self.manager.state.phase not in {Phase.READY, Phase.DEGRADED}:
                raise ValueError("Task feature is not ready")
            if not bool(self.events.latest.get("pose", {}).get("localized", False)):
                raise ValueError("Wait for the map default pose to initialize")
            profile = self._active_profile(str(payload.get("map_name", "")))
            route_id = str(payload.get("route_id", ""))
            route = next((item for item in profile["routes"] if item["id"] == route_id), None)
            if route is None:
                raise ValueError("Unknown route")
            points_by_id = {item["id"]: item for item in profile["waypoints"]}
            points = [points_by_id[item] for item in route["waypoint_ids"]]
            await self.bridge.send(
                {"type": "route_start", "name": route["name"], "points": points}
            )
            return
        if command_type == "clear_emergency" and self.manager.state.phase not in {Phase.READY, Phase.DEGRADED}:
            raise ValueError("Emergency stop can only be cleared for a ready feature")
        await self.bridge.send(payload)

    def _active_profile(self, map_name: str) -> dict[str, Any]:
        if not map_name or map_name != self.maps.active_name():
            raise ValueError("Selected map is not active")
        return self.maps.get(map_name)

    def map_profiles(self) -> dict[str, Any]:
        return {
            "maps": self.maps.list_profiles(),
            "active_map": self.maps.active_name(),
        }

    def update_map(self, map_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        profile = self.maps.update(map_name, payload)
        return {"map": profile, **self.map_profiles()}

    async def activate_map(self, map_name: str) -> dict[str, Any]:
        if self.manager.state.feature not in {"IDLE", "WEB_MANUAL", "SLAM"}:
            raise ValueError("Stop navigation before selecting a different map")
        if self.manager.state.phase in {Phase.STARTING, Phase.STOPPING}:
            raise ValueError("Wait for the current feature transition to finish")
        profile = self.maps.get(map_name)
        target = self.settings.map_save.target
        previous = await self.supervisor.target_status(target)
        if not previous.running:
            await self.supervisor.ensure_target(target)
        try:
            command = self.settings.map_save.activate_command_template.format(
                yaml_path=shlex.quote(profile["yaml_path"])
            )
            await self.supervisor.run_once(target, "select_map", command, 12)
        finally:
            if not previous.running:
                await self.supervisor.stop_target(target)
        self.maps.set_active(map_name)
        await self.events.publish(
            {"type": "log", "level": "INFO", "message": f"Active map: {map_name}"}
        )
        return self.map_profiles()

    async def save_map(self, map_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not MAP_NAME.fullmatch(map_name):
            raise ValueError("Map name must contain only letters, numbers, underscore or dash")
        if self.manager.state.feature not in self.settings.map_save.allowed_features:
            raise ValueError("Map save is only available while mapping")
        profile = self.maps.prepare_new_map(map_name, payload)
        command = self.settings.map_save.command_template.format(map_name=map_name)
        result = await self.supervisor.run_once(
            self.settings.map_save.target,
            f"save_map_{map_name}",
            command,
            self.settings.map_save.timeout_seconds,
        )
        self.maps.commit(profile)
        selection = self.settings.map_save.activate_command_template.format(
            yaml_path=shlex.quote(profile["yaml_path"])
        )
        await self.supervisor.run_once(
            self.settings.map_save.target,
            "select_saved_map",
            selection,
            12,
        )
        self.maps.set_active(map_name)
        await self.events.publish({"type": "log", "level": "INFO", "message": f"Map saved: {map_name}"})
        return {"output": result.stdout, "map": profile, **self.map_profiles()}

    async def client_connected(self) -> None:
        self._clients += 1

    async def client_disconnected(self) -> None:
        self._clients = max(0, self._clients - 1)
        if not self.bridge.available:
            return
        try:
            await self.bridge.send({"type": "drive", "linear": 0.0, "angular": 0.0})
            if self._clients == 0 and self.manager.state.feature in {"WEB_MANUAL", "SLAM"}:
                await self.bridge.send({"type": "emergency_stop"})
        except RuntimeError:
            pass

    async def component_log(self, component: str, lines: int) -> str:
        if component not in self.settings.components:
            raise ValueError(f"Unknown component: {component}")
        return await self.supervisor.tail(component, max(1, min(lines, 500)))
