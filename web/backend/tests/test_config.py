from __future__ import annotations

import unittest
from pathlib import Path

from icar_web.config import feature_components, load_settings


CONFIG = Path(__file__).resolve().parents[2] / "config" / "system.json"


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings(CONFIG)

    def test_feature_registry_uses_atomic_commands(self) -> None:
        forbidden_commands = {"m1", "m2", "m3", "m4", "n1", "n2", "n3", "n4", "n5"}
        for component in self.settings.components.values():
            self.assertNotIn(component.command.strip(), forbidden_commands)
            self.assertNotIn("rviz", component.command.lower())

    def test_navigation_closure_contains_real_nodes(self) -> None:
        closure = feature_components(self.settings, "NAV_DWA")
        nodes = {
            node
            for component in closure
            for node in self.settings.components[component].nodes
        }
        self.assertIn("/driver_node", nodes)
        self.assertIn("/base_node", nodes)
        self.assertIn("/map_server", nodes)
        self.assertIn("/amcl", nodes)
        self.assertIn("/controller_server", nodes)
        self.assertNotIn("Mcnamu_driver_X3", nodes)

    def test_map_servers_use_an_absolute_map_without_relative_override(self) -> None:
        for name in ("nav2_map_server_dwa", "nav2_map_server_teb"):
            command = self.settings.components[name].command
            self.assertEqual(command, "bash /opt/icar-web/ros/start_map_server.sh")
        self.assertIn("/root/maps/{map_name}", self.settings.map_save.command_template)
        self.assertIn("save_map_timeout:=15000", self.settings.map_save.command_template)
        self.assertIn("map_subscribe_transient_local:=true", self.settings.map_save.command_template)
        self.assertTrue(self.settings.map_save.default_map_yaml.startswith("/root/"))

    def test_velocity_sources_are_remapped(self) -> None:
        self.assertIn("cmd_vel:=/cmd_vel/nav", self.settings.components["nav2_controller_dwa"].command)
        self.assertIn("/cmd_vel:=/cmd_vel/behavior", self.settings.components["behavior_laser_avoid"].command)
        nav_driver = self.settings.components["nav_driver"]
        self.assertIn("/cmd_vel:=/cmd_vel/hardware", nav_driver.command)
        self.assertIn("/vel_raw:=/vel_raw/hardware", nav_driver.command)
        self.assertEqual(nav_driver.depends_on, ("nav_motion_adapter",))
        self.assertEqual(
            self.settings.components["nav_motion_adapter"].depends_on,
            ("nav_velocity_arbiter",),
        )

    def test_visual_tracking_is_explicitly_blocked(self) -> None:
        feature = self.settings.features["VISUAL_TRACK"]
        self.assertFalse(feature.enabled)
        self.assertIn("/dev/video0", feature.blocked_reason)

    def test_legacy_behaviors_are_hidden_and_route_task_is_distinct(self) -> None:
        for name in ("LASER_AVOID", "LASER_TRACK", "LASER_GUARD", "VISUAL_TRACK"):
            self.assertFalse(self.settings.features[name].visible)
        task = self.settings.features["TASK_ROUTE"]
        self.assertTrue(task.visible)
        self.assertEqual(task.surface, "task")
        closure = feature_components(self.settings, "TASK_ROUTE")
        self.assertIn("nav_route_task", closure)
        self.assertIn("nav2_controller_dwa", closure)
        self.assertNotEqual(
            set(closure),
            set(feature_components(self.settings, "NAV_DWA")),
        )


if __name__ == "__main__":
    unittest.main()
