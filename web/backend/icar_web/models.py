from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class HealthLevel(str, Enum):
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TargetConfig:
    name: str
    kind: str
    container: str
    runner_path: str
    bridge_path: str
    hardware: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardwareConfig:
    name: str
    label: str
    host_path: str
    required_for_motion: bool = False


@dataclass(frozen=True)
class ComponentConfig:
    name: str
    target: str
    command: str
    nodes: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    lifecycle_node: str | None = None


@dataclass(frozen=True)
class FeatureConfig:
    name: str
    label: str
    group: str
    surface: str
    components: tuple[str, ...]
    control_source: str
    enabled: bool = True
    blocked_reason: str = ""
    visible: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.name,
            "label": self.label,
            "group": self.group,
            "surface": self.surface,
            "control_source": self.control_source,
            "enabled": self.enabled,
            "blocked_reason": self.blocked_reason,
            "visible": self.visible,
        }


@dataclass(frozen=True)
class RuntimeConfig:
    docker_binary: str = "docker"
    command_timeout_seconds: float = 12.0
    start_settle_seconds: float = 0.8
    health_interval_seconds: float = 2.0
    readiness_grace_seconds: float = 12.0
    stop_inactive_containers: bool = True


@dataclass(frozen=True)
class MapSaveConfig:
    target: str
    allowed_features: tuple[str, ...]
    command_template: str
    timeout_seconds: float
    host_directory: str
    container_directory: str
    default_map_name: str
    default_map_yaml: str
    activate_command_template: str


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    runtime: RuntimeConfig
    hardware: dict[str, HardwareConfig]
    targets: dict[str, TargetConfig]
    components: dict[str, ComponentConfig]
    features: dict[str, FeatureConfig]
    map_save: MapSaveConfig
    health_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class ProcessState:
    name: str
    target: str
    running: bool
    pid: int | None = None
    detail: str = ""
    exit_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target": self.target,
            "running": self.running,
            "pid": self.pid,
            "detail": self.detail,
            "exit_code": self.exit_code,
        }


@dataclass
class TargetState:
    name: str
    container: str
    running: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "container": self.container,
            "running": self.running,
            "detail": self.detail,
        }


@dataclass
class HealthSnapshot:
    overall: HealthLevel = HealthLevel.UNKNOWN
    groups: dict[str, HealthLevel] = field(default_factory=dict)
    nodes: list[str] = field(default_factory=list)
    lifecycle: dict[str, str] = field(default_factory=dict)
    processes: dict[str, ProcessState] = field(default_factory=dict)
    targets: dict[str, TargetState] = field(default_factory=dict)
    hardware: dict[str, dict[str, Any]] = field(default_factory=dict)
    topic_freshness: dict[str, float | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    checked_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall.value,
            "groups": {key: value.value for key, value in self.groups.items()},
            "nodes": self.nodes,
            "lifecycle": self.lifecycle,
            "processes": {key: value.as_dict() for key, value in self.processes.items()},
            "targets": {key: value.as_dict() for key, value in self.targets.items()},
            "hardware": self.hardware,
            "topic_freshness": self.topic_freshness,
            "warnings": self.warnings,
            "checked_at": self.checked_at,
        }


@dataclass
class RuntimeState:
    feature: str = "IDLE"
    phase: Phase = Phase.IDLE
    generation: int = 0
    active_components: list[str] = field(default_factory=list)
    message: str = ""
    changed_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "phase": self.phase.value,
            "generation": self.generation,
            "active_components": self.active_components,
            "message": self.message,
            "changed_at": self.changed_at,
        }
