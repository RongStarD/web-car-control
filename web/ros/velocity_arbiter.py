#!/usr/bin/env python3
from __future__ import annotations

import json
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String


class VelocityArbiter(Node):
    SOURCES = {
        "MANUAL": "/cmd_vel/manual",
        "NAV": "/cmd_vel/nav",
        "BEHAVIOR": "/cmd_vel/behavior",
    }

    def __init__(self) -> None:
        super().__init__("web_velocity_arbiter")
        self.declare_parameter("manual_timeout", 0.40)
        self.declare_parameter("nav_timeout", 0.75)
        self.declare_parameter("behavior_timeout", 0.75)
        self.declare_parameter("publish_rate", 20.0)

        self.mode = "IDLE"
        self.emergency = True
        self.commands: dict[str, tuple[Twist, float]] = {}
        self.output = self.create_publisher(Twist, "/cmd_vel", 10)
        self.status = self.create_publisher(String, "/web/velocity_arbiter/status", 10)
        control_qos = QoSProfile(depth=1)
        control_qos.reliability = ReliabilityPolicy.RELIABLE
        control_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        for source, topic in self.SOURCES.items():
            self.create_subscription(Twist, topic, self._command_callback(source), 10)
        self.create_subscription(String, "/web/control_mode", self._mode_callback, control_qos)
        self.create_subscription(Bool, "/web/emergency_stop", self._emergency_callback, control_qos)

        rate = max(5.0, float(self.get_parameter("publish_rate").value))
        self.create_timer(1.0 / rate, self._tick)
        self.create_timer(1.0, self._publish_status)

    def _command_callback(self, source: str):
        def callback(message: Twist) -> None:
            command = Twist()
            command.linear.x = float(message.linear.x)
            command.linear.y = float(message.linear.y)
            command.angular.z = float(message.angular.z)
            self.commands[source] = (command, time.monotonic())

        return callback

    def _mode_callback(self, message: String) -> None:
        requested = message.data.upper()
        self.mode = requested if requested in {*self.SOURCES, "IDLE"} else "IDLE"

    def _emergency_callback(self, message: Bool) -> None:
        self.emergency = bool(message.data)

    def _timeout(self, source: str) -> float:
        parameter = {
            "MANUAL": "manual_timeout",
            "NAV": "nav_timeout",
            "BEHAVIOR": "behavior_timeout",
        }[source]
        return float(self.get_parameter(parameter).value)

    def _tick(self) -> None:
        output = Twist()
        if not self.emergency and self.mode in self.SOURCES:
            command = self.commands.get(self.mode)
            if command and time.monotonic() - command[1] <= self._timeout(self.mode):
                output = command[0]
        self.output.publish(output)

    def _publish_status(self) -> None:
        now = time.monotonic()
        ages = {
            source.lower(): round(now - command[1], 3)
            for source, command in self.commands.items()
        }
        message = String()
        message.data = json.dumps(
            {"mode": self.mode, "emergency": self.emergency, "source_ages": ages},
            separators=(",", ":"),
        )
        self.status.publish(message)

    def stop(self) -> None:
        self.emergency = True
        zero = Twist()
        for _ in range(5):
            self.output.publish(zero)


def main() -> None:
    rclpy.init()
    node = VelocityArbiter()
    try:
        rclpy.spin(node)
    finally:
        node.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
