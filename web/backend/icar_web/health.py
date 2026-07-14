from __future__ import annotations

import time

from .models import HealthLevel, HealthSnapshot, Settings
from .supervisor import Supervisor


class HealthMonitor:
    def __init__(self, settings: Settings, supervisor: Supervisor) -> None:
        self.settings = settings
        self.supervisor = supervisor

    @staticmethod
    def _has_node(nodes: list[str], expected: str) -> bool:
        normalized = expected.rstrip("/") or "/"
        return any(node.rstrip("/") == normalized for node in nodes)

    async def inspect(
        self,
        active_components: list[str],
        topic_freshness: dict[str, float | None] | None = None,
    ) -> HealthSnapshot:
        warnings: list[str] = []
        processes = {}
        targets = {}
        nodes: list[str] = []
        lifecycle: dict[str, str] = {}
        hardware = await self.supervisor.hardware_status()

        active_targets = {
            self.settings.components[name].target
            for name in active_components
        }
        for target in self.settings.targets:
            targets[target] = await self.supervisor.target_status(target)

        for component in active_components:
            process = await self.supervisor.status(component)
            processes[component] = process
            if not process.running:
                detail = f" (exit {process.exit_code})" if process.exit_code is not None else ""
                warnings.append(f"Process {component} is not running{detail}")

        for target in active_targets:
            if not targets[target].running:
                warnings.append(f"Container {targets[target].container} is stopped")
                continue
            try:
                nodes.extend(await self.supervisor.nodes(target))
            except Exception as exc:
                warnings.append(f"ROS graph unavailable on {target}: {exc}")

        expected_nodes = {
            node
            for component in active_components
            for node in self.settings.components[component].nodes
        }
        missing_nodes = sorted(node for node in expected_nodes if not self._has_node(nodes, node))
        if missing_nodes:
            warnings.append("Missing ROS nodes: " + ", ".join(missing_nodes))

        required_resources = {
            resource
            for component in active_components
            for resource in self.settings.components[component].resources
        }
        missing_required_hardware = sorted(
            name
            for name in required_resources
            if not bool(hardware.get(name, {}).get("present"))
        )
        missing_idle_hardware = sorted(
            name
            for name, state in hardware.items()
            if bool(state.get("required_for_motion")) and not bool(state.get("present"))
        )
        if missing_required_hardware:
            warnings.append("Required hardware not detected: " + ", ".join(missing_required_hardware))
        elif not active_components and missing_idle_hardware:
            warnings.append("Motion hardware not detected: " + ", ".join(missing_idle_hardware))

        for component in active_components:
            config = self.settings.components[component]
            if config.lifecycle_node:
                state = await self.supervisor.lifecycle(config.target, config.lifecycle_node)
                lifecycle[config.lifecycle_node] = state
                if state != "active":
                    warnings.append(f"Lifecycle node {config.lifecycle_node} is {state}")

        groups: dict[str, HealthLevel] = {}
        for group, patterns in self.settings.health_groups.items():
            relevant = [pattern for pattern in patterns if pattern in expected_nodes]
            if not relevant:
                groups[group] = HealthLevel.UNKNOWN
            elif all(self._has_node(nodes, pattern) for pattern in relevant):
                groups[group] = HealthLevel.OK
            else:
                groups[group] = HealthLevel.WARN

        expected_topics = {
            topic
            for component in active_components
            for topic in self.settings.components[component].topics
        }
        freshness = {
            topic: age
            for topic, age in (topic_freshness or {}).items()
            if topic in expected_topics
        }
        # A map_server map is transient-local static data; only SLAM is expected to refresh it.
        if "nav_slam_gmapping" not in active_components:
            freshness.pop("/map", None)
        stale_topics = sorted(topic for topic, age in freshness.items() if age is not None and age > 3.0)
        if stale_topics:
            warnings.append("Stale data: " + ", ".join(stale_topics))

        hard_failure = any(not process.running for process in processes.values()) or any(
            not targets[target].running for target in active_targets
        ) or bool(missing_required_hardware)
        lifecycle_failure = any(state in {"finalized", "unavailable"} for state in lifecycle.values())
        if hard_failure or lifecycle_failure:
            overall = HealthLevel.ERROR
        elif warnings:
            overall = HealthLevel.WARN
        else:
            overall = HealthLevel.OK

        if not active_components and not warnings:
            overall = HealthLevel.OK

        return HealthSnapshot(
            overall=overall,
            groups=groups,
            nodes=sorted(set(nodes)),
            lifecycle=lifecycle,
            processes=processes,
            targets=targets,
            hardware=hardware,
            topic_freshness=freshness,
            warnings=warnings,
            checked_at=time.time(),
        )
