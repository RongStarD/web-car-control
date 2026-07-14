from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import (
    ComponentConfig,
    FeatureConfig,
    HardwareConfig,
    MapSaveConfig,
    RuntimeConfig,
    Settings,
    TargetConfig,
)


def _required(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"Missing required configuration key: {key}")
    return mapping[key]


def _validate_graph(components: dict[str, ComponentConfig]) -> None:
    visiting: set[str] = set()
    complete: set[str] = set()

    def visit(name: str) -> None:
        if name in complete:
            return
        if name in visiting:
            raise ValueError(f"Component dependency cycle includes {name}")
        visiting.add(name)
        for dependency in components[name].depends_on:
            if dependency not in components:
                raise ValueError(f"Component {name} references unknown dependency {dependency}")
            visit(dependency)
        visiting.remove(name)
        complete.add(name)

    for component_name in components:
        visit(component_name)


def load_settings(path: Path) -> Settings:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema_version", 0)) != 2:
        raise ValueError("system.json must use schema_version 2")

    server = _required(raw, "server")
    runtime = raw.get("runtime", {})

    hardware = {
        name: HardwareConfig(
            name=name,
            label=str(value.get("label", name)),
            host_path=str(_required(value, "host_path")),
            required_for_motion=bool(value.get("required_for_motion", False)),
        )
        for name, value in _required(raw, "hardware").items()
    }

    targets = {
        name: TargetConfig(
            name=name,
            kind=str(value.get("kind", "docker")),
            container=str(_required(value, "container")),
            runner_path=str(_required(value, "runner_path")),
            bridge_path=str(_required(value, "bridge_path")),
            hardware=tuple(value.get("hardware", [])),
        )
        for name, value in _required(raw, "targets").items()
    }

    components = {
        name: ComponentConfig(
            name=name,
            target=str(_required(value, "target")),
            command=str(_required(value, "command")),
            nodes=tuple(value.get("nodes", [])),
            topics=tuple(value.get("topics", [])),
            resources=tuple(value.get("resources", [])),
            depends_on=tuple(value.get("depends_on", [])),
            lifecycle_node=value.get("lifecycle_node"),
        )
        for name, value in _required(raw, "components").items()
    }
    for component in components.values():
        if component.target not in targets:
            raise ValueError(f"Component {component.name} references unknown target {component.target}")
        unknown_resources = set(component.resources) - set(hardware)
        if unknown_resources:
            raise ValueError(
                f"Component {component.name} references unknown hardware: {sorted(unknown_resources)}"
            )
    _validate_graph(components)

    features = {
        name: FeatureConfig(
            name=name,
            label=str(value.get("label", name)),
            group=str(value.get("group", "other")),
            surface=str(value.get("surface", "overview")),
            components=tuple(value.get("components", [])),
            control_source=str(value.get("control_source", "IDLE")),
            enabled=bool(value.get("enabled", True)),
            blocked_reason=str(value.get("blocked_reason", "")),
            visible=bool(value.get("visible", True)),
        )
        for name, value in _required(raw, "features").items()
    }
    if "IDLE" not in features:
        raise ValueError("Feature registry must include IDLE")
    for feature in features.values():
        unknown = set(feature.components) - set(components)
        if unknown:
            raise ValueError(f"Feature {feature.name} references unknown components: {sorted(unknown)}")

    map_save_raw = _required(raw, "map_save")
    map_target = str(_required(map_save_raw, "target"))
    if map_target not in targets:
        raise ValueError(f"Map save references unknown target {map_target}")

    return Settings(
        host=str(server.get("host", "0.0.0.0")),
        port=int(server.get("port", 8080)),
        runtime=RuntimeConfig(
            docker_binary=str(runtime.get("docker_binary", "docker")),
            command_timeout_seconds=float(runtime.get("command_timeout_seconds", 12)),
            start_settle_seconds=float(runtime.get("start_settle_seconds", 0.8)),
            health_interval_seconds=float(runtime.get("health_interval_seconds", 2)),
            readiness_grace_seconds=float(runtime.get("readiness_grace_seconds", 12)),
            stop_inactive_containers=bool(runtime.get("stop_inactive_containers", True)),
        ),
        hardware=hardware,
        targets=targets,
        components=components,
        features=features,
        map_save=MapSaveConfig(
            target=map_target,
            allowed_features=tuple(map_save_raw.get("allowed_features", ["SLAM"])),
            command_template=str(_required(map_save_raw, "command_template")),
            timeout_seconds=float(map_save_raw.get("timeout_seconds", 45)),
            host_directory=os.getenv(
                "ICAR_MAP_DIRECTORY",
                str(_required(map_save_raw, "host_directory")),
            ),
            container_directory=str(_required(map_save_raw, "container_directory")),
            default_map_name=str(_required(map_save_raw, "default_map_name")),
            default_map_yaml=str(_required(map_save_raw, "default_map_yaml")),
            activate_command_template=str(_required(map_save_raw, "activate_command_template")),
        ),
        health_groups={
            name: tuple(patterns)
            for name, patterns in raw.get("health_groups", {}).items()
        },
    )


def component_closure(settings: Settings, component_names: tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        for dependency in settings.components[name].depends_on:
            visit(dependency)
        visited.add(name)
        ordered.append(name)

    for component_name in component_names:
        visit(component_name)
    return ordered


def feature_components(settings: Settings, feature_name: str) -> list[str]:
    return component_closure(settings, settings.features[feature_name].components)
