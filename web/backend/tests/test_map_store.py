from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from icar_web.config import load_settings
from icar_web.map_store import MapStore


CONFIG = Path(__file__).resolve().parents[2] / "config" / "system.json"


class MapStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        settings = load_settings(CONFIG)
        config = replace(settings.map_save, host_directory=self.temporary.name)
        self.store = MapStore(config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def profile() -> dict:
        return {
            "label": "Hotel floor",
            "waypoints": [
                {"id": "kitchen", "name": "Kitchen", "x": 1, "y": 2, "yaw": 0.5},
                {"id": "room_808", "name": "Room 808", "x": 6, "y": 4, "yaw": 1.2},
            ],
            "default_pose_id": "kitchen",
            "routes": [
                {
                    "id": "delivery_808",
                    "name": "Delivery 808",
                    "waypoint_ids": ["kitchen", "room_808", "kitchen"],
                }
            ],
        }

    def write_map_files(self, name: str) -> None:
        Path(self.temporary.name, f"{name}.yaml").write_text(
            f"image: {name}.pgm\nresolution: 0.05\n",
            encoding="utf-8",
        )
        Path(self.temporary.name, f"{name}.pgm").write_bytes(b"P5\n1 1\n255\n\x00")

    def test_profile_is_saved_next_to_map_and_round_trips(self) -> None:
        saved = self.store.save_new_map("floor_8", self.profile())
        self.assertEqual(saved["yaml_path"], "/root/maps/floor_8.yaml")
        self.assertTrue(Path(self.temporary.name, "floor_8.ohcar.json").is_file())
        loaded = self.store.get("floor_8")
        self.assertEqual(loaded["default_pose_id"], "kitchen")
        self.assertEqual(
            loaded["routes"][0]["waypoint_ids"],
            ["kitchen", "room_808", "kitchen"],
        )

    def test_default_pose_and_routes_must_reference_recorded_points(self) -> None:
        invalid_default = self.profile()
        invalid_default["default_pose_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "default_pose_id"):
            self.store.prepare_new_map("floor_8", invalid_default)

        invalid_route = self.profile()
        invalid_route["routes"][0]["waypoint_ids"] = ["kitchen", "missing"]
        with self.assertRaisesRegex(ValueError, "unknown waypoints"):
            self.store.prepare_new_map("floor_8", invalid_route)

    def test_active_map_is_persisted(self) -> None:
        self.store.save_new_map("floor_8", self.profile())
        self.write_map_files("floor_8")
        self.store.set_active("floor_8")
        self.assertEqual(self.store.active_name(), "floor_8")

    def test_incomplete_map_is_reported_and_not_used_as_active(self) -> None:
        profile = self.store.save_new_map("floor_8", self.profile())
        self.store.set_active("floor_8")

        listed = next(item for item in self.store.list_profiles() if item["map_name"] == "floor_8")
        self.assertFalse(listed["available"])
        self.assertEqual(self.store.active_name(), "yahboomcar")
        with self.assertRaisesRegex(ValueError, "floor_8.yaml, floor_8.pgm"):
            self.store.require_files(profile)

        self.write_map_files("floor_8")
        self.assertTrue(self.store.list_profiles()[-1]["available"])
        self.assertEqual(self.store.active_name(), "floor_8")

    def test_save_must_update_existing_map_files(self) -> None:
        profile = self.store.prepare_new_map("floor_8", self.profile())
        self.write_map_files("floor_8")
        previous = self.store.file_signatures(profile)

        with self.assertRaisesRegex(ValueError, "stale files"):
            self.store.require_updated_files(profile, previous)

        Path(self.temporary.name, "floor_8.pgm").write_bytes(b"P5\n2 1\n255\n\x00\x00")
        Path(self.temporary.name, "floor_8.yaml").write_text(
            "image: floor_8.pgm\nresolution: 0.1\n",
            encoding="utf-8",
        )
        self.assertEqual(self.store.require_updated_files(profile, previous), profile)
