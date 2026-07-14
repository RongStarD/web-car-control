from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from .models import MapSaveConfig


NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MapStore:
    def __init__(self, config: MapSaveConfig) -> None:
        self.config = config
        self.directory = Path(config.host_directory)
        self.active_path = self.directory / ".ohcar-active"

    def _profile_path(self, name: str) -> Path:
        if not NAME.fullmatch(name):
            raise ValueError("Map name must contain only letters, numbers, underscore or dash")
        return self.directory / f"{name}.ohcar.json"

    def _built_in(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "map_name": self.config.default_map_name,
            "label": "Default map",
            "yaml_path": self.config.default_map_yaml,
            "built_in": True,
            "waypoints": [],
            "default_pose_id": None,
            "routes": [],
            "created_at": 0.0,
            "updated_at": 0.0,
        }

    @staticmethod
    def _number(value: Any, field: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} must be a finite number")
        return number

    def _normalize(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        yaml_path: str,
        created_at: float | None = None,
        built_in: bool = False,
    ) -> dict[str, Any]:
        self._profile_path(name)
        raw_waypoints = payload.get("waypoints", [])
        raw_routes = payload.get("routes", [])
        if not isinstance(raw_waypoints, list) or len(raw_waypoints) > 300:
            raise ValueError("waypoints must be a list with at most 300 items")
        if not isinstance(raw_routes, list) or len(raw_routes) > 100:
            raise ValueError("routes must be a list with at most 100 items")

        waypoints: list[dict[str, Any]] = []
        waypoint_ids: set[str] = set()
        for index, raw in enumerate(raw_waypoints):
            if not isinstance(raw, dict):
                raise ValueError(f"waypoints[{index}] must be an object")
            waypoint_id = str(raw.get("id", ""))
            if not NAME.fullmatch(waypoint_id) or waypoint_id in waypoint_ids:
                raise ValueError(f"Invalid or duplicate waypoint id: {waypoint_id}")
            label = str(raw.get("name", "")).strip()
            if not label or len(label) > 64:
                raise ValueError(f"waypoints[{index}].name must contain 1-64 characters")
            waypoint_ids.add(waypoint_id)
            waypoints.append(
                {
                    "id": waypoint_id,
                    "name": label,
                    "x": self._number(raw.get("x"), f"waypoints[{index}].x"),
                    "y": self._number(raw.get("y"), f"waypoints[{index}].y"),
                    "yaw": self._number(raw.get("yaw", 0.0), f"waypoints[{index}].yaw"),
                }
            )

        default_pose_id = payload.get("default_pose_id")
        if default_pose_id is not None:
            default_pose_id = str(default_pose_id)
            if default_pose_id not in waypoint_ids:
                raise ValueError("default_pose_id must reference an existing waypoint")

        routes: list[dict[str, Any]] = []
        route_ids: set[str] = set()
        for index, raw in enumerate(raw_routes):
            if not isinstance(raw, dict):
                raise ValueError(f"routes[{index}] must be an object")
            route_id = str(raw.get("id", ""))
            if not NAME.fullmatch(route_id) or route_id in route_ids:
                raise ValueError(f"Invalid or duplicate route id: {route_id}")
            label = str(raw.get("name", "")).strip()
            point_ids = raw.get("waypoint_ids", [])
            if not label or len(label) > 64:
                raise ValueError(f"routes[{index}].name must contain 1-64 characters")
            if not isinstance(point_ids, list) or not point_ids or len(point_ids) > 100:
                raise ValueError(f"routes[{index}].waypoint_ids must contain 1-100 items")
            normalized_ids = [str(item) for item in point_ids]
            unknown = [item for item in normalized_ids if item not in waypoint_ids]
            if unknown:
                raise ValueError(f"Route {route_id} references unknown waypoints: {unknown}")
            route_ids.add(route_id)
            routes.append({"id": route_id, "name": label, "waypoint_ids": normalized_ids})

        now = time.time()
        label = str(payload.get("label", name)).strip() or name
        return {
            "schema_version": 1,
            "map_name": name,
            "label": label[:64],
            "yaml_path": yaml_path,
            "built_in": built_in,
            "waypoints": waypoints,
            "default_pose_id": default_pose_id,
            "routes": routes,
            "created_at": now if created_at is None else created_at,
            "updated_at": now,
        }

    def _read(self, path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid map profile: {path.name}")
        name = path.name[: -len(".ohcar.json")]
        profile = self._normalize(
            name,
            raw,
            yaml_path=str(raw.get("yaml_path", "")),
            created_at=self._number(raw.get("created_at", 0.0), "created_at"),
            built_in=bool(raw.get("built_in", False)),
        )
        profile["updated_at"] = self._number(raw.get("updated_at", 0.0), "updated_at")
        return profile

    def _write(self, profile: dict[str, Any]) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._profile_path(profile["map_name"])
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return profile

    def list_profiles(self) -> list[dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {
            self.config.default_map_name: self._built_in()
        }
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.ohcar.json")):
                try:
                    profile = self._read(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                profiles[profile["map_name"]] = profile
        return sorted(profiles.values(), key=lambda item: (not item["built_in"], item["label"]))

    def get(self, name: str) -> dict[str, Any]:
        path = self._profile_path(name)
        if path.is_file():
            return self._read(path)
        if name == self.config.default_map_name:
            return self._built_in()
        raise ValueError(f"Unknown map: {name}")

    def save_new_map(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write(self.prepare_new_map(name, payload))

    def prepare_new_map(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        yaml_path = f"{self.config.container_directory.rstrip('/')}/{name}.yaml"
        return self._normalize(name, payload, yaml_path=yaml_path)

    def commit(self, profile: dict[str, Any]) -> dict[str, Any]:
        return self._write(profile)

    def update(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get(name)
        return self._write(
            self._normalize(
                name,
                payload,
                yaml_path=current["yaml_path"],
                created_at=current["created_at"],
                built_in=current["built_in"],
            )
        )

    def active_name(self) -> str:
        if self.active_path.is_file():
            name = self.active_path.read_text(encoding="utf-8").strip()
            try:
                self.get(name)
                return name
            except ValueError:
                pass
        return self.config.default_map_name

    def set_active(self, name: str) -> dict[str, Any]:
        profile = self.get(name)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.active_path.with_suffix(".tmp")
        temporary.write_text(name + "\n", encoding="utf-8")
        temporary.replace(self.active_path)
        return profile
