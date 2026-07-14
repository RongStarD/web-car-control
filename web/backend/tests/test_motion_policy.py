from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROS_ROOT = Path(__file__).resolve().parents[2] / "ros"
sys.path.insert(0, str(ROS_ROOT))

from motion_policy import velocity_source


class MotionPolicyTests(unittest.TestCase):
    def test_navigation_always_uses_fresh_hardware_feedback(self) -> None:
        self.assertEqual(velocity_source("NAV", 0.01, 0.02, 0.4, 0.2), "feedback")

    def test_navigation_never_substitutes_a_fresh_command_for_stale_feedback(self) -> None:
        self.assertEqual(velocity_source("NAV", 0.01, 0.21, 0.4, 0.2), "zero")

    def test_manual_mode_preserves_command_based_mapping_odometry(self) -> None:
        self.assertEqual(velocity_source("MANUAL", 0.01, 0.02, 0.4, 0.2), "command")

    def test_manual_mode_falls_back_to_fresh_feedback(self) -> None:
        self.assertEqual(velocity_source("MANUAL", 0.41, 0.02, 0.4, 0.2), "feedback")


if __name__ == "__main__":
    unittest.main()
