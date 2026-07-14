#!/usr/bin/env python3
from __future__ import annotations

import json
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class TaskGuard(Node):
    def __init__(self) -> None:
        super().__init__("web_route_task")
        self.publisher = self.create_publisher(String, "/web/route_task/status", 5)
        self.create_timer(1.0, self.publish_status)

    def publish_status(self) -> None:
        message = String()
        message.data = json.dumps({"ready": True, "stamp": time.time()}, separators=(",", ":"))
        self.publisher.publish(message)


def main() -> None:
    rclpy.init()
    node = TaskGuard()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
