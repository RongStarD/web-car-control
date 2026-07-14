#!/usr/bin/env python3
from __future__ import annotations

import math
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from motion_policy import velocity_source

LINEAR_RESPONSE_SECONDS = 0.12
ANGULAR_RESPONSE_SECONDS = 0.25


def copied_twist(message: Twist) -> Twist:
    copied = Twist()
    copied.linear.x = float(message.linear.x)
    copied.linear.y = float(message.linear.y)
    copied.linear.z = float(message.linear.z)
    copied.angular.x = float(message.angular.x)
    copied.angular.y = float(message.angular.y)
    copied.angular.z = float(message.angular.z)
    return copied


class MotionConventionAdapter(Node):
    def __init__(self) -> None:
        super().__init__("web_motion_convention")
        self.command_timeout = 0.4
        self.feedback_timeout = 0.2
        self.latest_command = Twist()
        self.latest_feedback = Twist()
        self.estimated_velocity = Twist()
        self.control_mode = "IDLE"
        self.command_seen_at = float("-inf")
        self.feedback_seen_at = float("-inf")
        self.velocity_updated_at = time.monotonic()
        self.hardware_command = self.create_publisher(Twist, "/cmd_vel/hardware", 20)
        self.ros_velocity = self.create_publisher(Twist, "/vel_raw", 20)
        self.create_subscription(Twist, "/cmd_vel", self._command, 20)
        self.create_subscription(Twist, "/vel_raw/hardware", self._feedback, 20)
        control_qos = QoSProfile(depth=1)
        control_qos.reliability = ReliabilityPolicy.RELIABLE
        control_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.create_subscription(String, "/web/control_mode", self._control_mode, control_qos)
        self.create_timer(0.04, self._publish_velocity)

    def _command(self, message: Twist) -> None:
        self.latest_command = copied_twist(message)
        self.command_seen_at = time.monotonic()
        self.hardware_command.publish(copied_twist(message))

    def _feedback(self, message: Twist) -> None:
        self.latest_feedback = copied_twist(message)
        self.feedback_seen_at = time.monotonic()

    def _control_mode(self, message: String) -> None:
        requested = message.data.upper()
        self.control_mode = requested if requested in {"IDLE", "MANUAL", "NAV", "BEHAVIOR"} else "IDLE"

    def _publish_velocity(self) -> None:
        now = time.monotonic()
        source = velocity_source(
            self.control_mode,
            now - self.command_seen_at,
            now - self.feedback_seen_at,
            self.command_timeout,
            self.feedback_timeout,
        )
        if source == "command":
            target = self.latest_command
        elif source == "feedback":
            target = self.latest_feedback
        else:
            target = Twist()

        if self.control_mode == "NAV":
            self.estimated_velocity = copied_twist(target)
            self.velocity_updated_at = now
            self.ros_velocity.publish(copied_twist(target))
            return

        elapsed = min(0.2, max(0.0, now - self.velocity_updated_at))
        self.velocity_updated_at = now
        linear_alpha = 1.0 - math.exp(-elapsed / LINEAR_RESPONSE_SECONDS)
        angular_alpha = 1.0 - math.exp(-elapsed / ANGULAR_RESPONSE_SECONDS)
        self.estimated_velocity.linear.x += linear_alpha * (
            target.linear.x - self.estimated_velocity.linear.x
        )
        self.estimated_velocity.linear.y += linear_alpha * (
            target.linear.y - self.estimated_velocity.linear.y
        )
        self.estimated_velocity.angular.z += angular_alpha * (
            target.angular.z - self.estimated_velocity.angular.z
        )
        self.ros_velocity.publish(copied_twist(self.estimated_velocity))

    def stop(self) -> None:
        for _ in range(5):
            self.hardware_command.publish(Twist())


def main() -> None:
    rclpy.init()
    node = MotionConventionAdapter()
    try:
        rclpy.spin(node)
    finally:
        node.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
