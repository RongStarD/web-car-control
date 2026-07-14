#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
from typing import Any

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav2_msgs.srv import ClearEntireCostmap
from nav_msgs.msg import OccupancyGrid, Odometry, Path
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import Bool, Float32, String
from std_srvs.srv import Empty
from tf2_ros import Buffer, TransformListener


class JsonEmitter:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def send(self, event: dict[str, Any]) -> None:
        with self._lock:
            sys.stdout.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n")
            sys.stdout.flush()


class WebRosBridge(Node):
    def __init__(self, emitter: JsonEmitter, commands: queue.Queue[dict[str, Any]]) -> None:
        super().__init__("web_ros_bridge")
        self.emitter = emitter
        self.commands = commands
        self.goal_handle = None
        self.route_token = 0
        self.route_name = ""
        self.route_points: list[dict[str, Any]] = []
        self.route_index = -1
        self.last_drive = 0.0
        self.voltage: float | None = None
        self.linear = 0.0
        self.angular = 0.0
        self.imu_yaw: float | None = None
        self.imu_angular_z = 0.0
        self.imu_seen_at = float("-inf")
        self.imu_map_offset: float | None = None
        self.control_source = "IDLE"
        self.localization_initialized = False
        self.localization_pending = False
        self.localization_requested_at = 0.0
        self.localization_update_requested_at = 0.0
        self.started = time.monotonic()
        self.last_seen: dict[str, float] = {}
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        transient = QoSProfile(depth=1)
        transient.reliability = ReliabilityPolicy.RELIABLE
        transient.durability = DurabilityPolicy.TRANSIENT_LOCAL
        sensor = QoSProfile(depth=5)
        sensor.reliability = ReliabilityPolicy.BEST_EFFORT

        self.manual = self.create_publisher(Twist, "/cmd_vel/manual", 10)
        self.control_mode = self.create_publisher(String, "/web/control_mode", transient)
        self.emergency = self.create_publisher(Bool, "/web/emergency_stop", transient)
        self.initial_pose = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self.navigation = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self.nomotion_update = self.create_client(Empty, "/request_nomotion_update")
        self.clear_local_costmap = self.create_client(
            ClearEntireCostmap, "/local_costmap/clear_entirely_local_costmap"
        )
        self.clear_global_costmap = self.create_client(
            ClearEntireCostmap, "/global_costmap/clear_entirely_global_costmap"
        )

        self.create_subscription(OccupancyGrid, "/map", lambda msg: self._grid("map", msg), transient)
        self.create_subscription(OccupancyGrid, "/local_costmap/costmap", lambda msg: self._grid("local_costmap", msg), 2)
        self.create_subscription(OccupancyGrid, "/global_costmap/costmap", lambda msg: self._grid("global_costmap", msg), 2)
        self.create_subscription(Odometry, "/odom", self._odom, 10)
        self.create_subscription(Imu, "/imu/data", self._imu, sensor)
        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self._amcl_pose, 10)
        self.create_subscription(LaserScan, "/scan", self._scan, sensor)
        self.create_subscription(Path, "/plan", self._path, 5)
        self.create_subscription(Float32, "/voltage", self._voltage, 5)
        self.create_subscription(String, "/web/velocity_arbiter/status", self._arbiter_status, 5)

        self.create_timer(0.02, self._drain_commands)
        self.create_timer(0.05, self._drive_watchdog)
        self.create_timer(0.5, self._localization_tick)
        self.create_timer(1.0, self._telemetry)

    @staticmethod
    def _yaw(z: float, w: float) -> float:
        return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)

    @staticmethod
    def _normalize_angle(angle: float) -> float:
        return math.atan2(math.sin(angle), math.cos(angle))

    @staticmethod
    def _quaternion_yaw(quaternion) -> float:
        return math.atan2(
            2.0 * (quaternion.w * quaternion.z + quaternion.x * quaternion.y),
            1.0 - 2.0 * (quaternion.y * quaternion.y + quaternion.z * quaternion.z),
        )

    def _visual_yaw(self, reference_yaw: float) -> tuple[float, str]:
        if self.control_source == "NAV":
            return reference_yaw, "tf"
        if self.imu_yaw is None or time.monotonic() - self.imu_seen_at > 0.25:
            self.imu_map_offset = None
            return reference_yaw, "odom"
        if self.imu_map_offset is None:
            self.imu_map_offset = self._normalize_angle(reference_yaw - self.imu_yaw)
        return self._normalize_angle(self.imu_yaw + self.imu_map_offset), "imu"

    @staticmethod
    def _stamp_time(message) -> Time:
        stamp = message.header.stamp
        if stamp.sec == 0 and stamp.nanosec == 0:
            return Time()
        return Time.from_msg(stamp)

    @staticmethod
    def _rle(values: list[int]) -> list[list[int]]:
        result: list[list[int]] = []
        for raw in values:
            value = int(raw)
            if result and result[-1][0] == value:
                result[-1][1] += 1
            else:
                result.append([value, 1])
        return result

    def _mark(self, name: str) -> None:
        self.last_seen[name] = time.monotonic()

    @staticmethod
    def _rotate_xy(x: float, y: float, quaternion) -> tuple[float, float]:
        rotated_x = (
            (1.0 - 2.0 * (quaternion.y * quaternion.y + quaternion.z * quaternion.z)) * x
            + 2.0 * (quaternion.x * quaternion.y - quaternion.z * quaternion.w) * y
        )
        rotated_y = (
            2.0 * (quaternion.x * quaternion.y + quaternion.z * quaternion.w) * x
            + (1.0 - 2.0 * (quaternion.x * quaternion.x + quaternion.z * quaternion.z)) * y
        )
        return rotated_x, rotated_y

    def _grid(self, event_type: str, message: OccupancyGrid) -> None:
        self._mark(event_type)
        origin = message.info.origin
        frame = message.header.frame_id or "map"
        origin_x = float(origin.position.x)
        origin_y = float(origin.position.y)
        origin_yaw = self._yaw(origin.orientation.z, origin.orientation.w)
        if frame != "map":
            try:
                transform = self.tf_buffer.lookup_transform("map", frame, Time()).transform
                origin_x, origin_y = self._rotate_xy(origin_x, origin_y, transform.rotation)
                origin_x += float(transform.translation.x)
                origin_y += float(transform.translation.y)
                origin_yaw += self._yaw(transform.rotation.z, transform.rotation.w)
                frame = "map"
            except Exception:
                pass
        self.emitter.send(
            {
                "type": event_type,
                "width": int(message.info.width),
                "height": int(message.info.height),
                "resolution": float(message.info.resolution),
                "origin": {
                    "x": origin_x,
                    "y": origin_y,
                    "yaw": origin_yaw,
                },
                "frame": frame,
                "data_rle": self._rle(message.data),
                "stamp": time.time(),
            }
        )

    def _odom(self, message: Odometry) -> None:
        self._mark("pose")
        pose = message.pose.pose
        self.linear = float(message.twist.twist.linear.x)
        self.angular = float(message.twist.twist.angular.z)
        frame = message.header.frame_id or "odom"
        x = float(pose.position.x)
        y = float(pose.position.y)
        yaw = self._yaw(pose.orientation.z, pose.orientation.w)
        stamp = self._stamp_time(message)
        if frame != "map":
            transform = None
            for lookup_time in (stamp, Time()):
                try:
                    transform = self.tf_buffer.lookup_transform(
                        "map", frame, lookup_time, Duration(seconds=0.05)
                    ).transform
                    break
                except Exception:
                    continue
            if transform is not None:
                x, y = self._rotate_xy(x, y, transform.rotation)
                x += float(transform.translation.x)
                y += float(transform.translation.y)
                yaw += self._yaw(transform.rotation.z, transform.rotation.w)
                yaw = self._normalize_angle(yaw)
                frame = "map"
        yaw_source = "odom"
        if frame == "map":
            yaw, yaw_source = self._visual_yaw(yaw)
        self.emitter.send(
            {
                "type": "pose",
                "x": x,
                "y": y,
                "yaw": yaw,
                "yaw_source": yaw_source,
                "frame": frame,
                "localized": self.localization_initialized,
                "stamp": time.time(),
            }
        )

    def _imu(self, message: Imu) -> None:
        self._mark("imu")
        self.imu_yaw = self._quaternion_yaw(message.orientation)
        self.imu_angular_z = float(message.angular_velocity.z)
        self.imu_seen_at = time.monotonic()

    def _localization_event(self, state: str, **extra: Any) -> None:
        self.emitter.send(
            {
                "type": "localization",
                "state": state,
                "localized": self.localization_initialized,
                **extra,
                "stamp": time.time(),
            }
        )

    def _amcl_pose(self, message: PoseWithCovarianceStamped) -> None:
        self._mark("amcl_pose")
        if not self.localization_pending:
            return
        pose = message.pose.pose
        self.localization_pending = False
        self.localization_initialized = True
        self._localization_event(
            "ready",
            x=float(pose.position.x),
            y=float(pose.position.y),
            yaw=self._yaw(pose.orientation.z, pose.orientation.w),
            position_variance=max(
                float(message.pose.covariance[0]),
                float(message.pose.covariance[7]),
            ),
            yaw_variance=float(message.pose.covariance[35]),
        )
        request = ClearEntireCostmap.Request()
        if self.clear_local_costmap.service_is_ready():
            self.clear_local_costmap.call_async(request)
        if self.clear_global_costmap.service_is_ready():
            self.clear_global_costmap.call_async(ClearEntireCostmap.Request())

    def _localization_tick(self) -> None:
        if not self.localization_pending:
            return
        now = time.monotonic()
        elapsed = now - self.localization_requested_at
        if elapsed >= 10.0:
            self.localization_pending = False
            self.localization_initialized = False
            self._localization_event("timeout", detail="AMCL did not return a pose")
            return
        if now - self.localization_update_requested_at >= 1.0:
            self.localization_update_requested_at = now
            if self.nomotion_update.service_is_ready():
                self.nomotion_update.call_async(Empty.Request())

    def _scan(self, message: LaserScan) -> None:
        self._mark("scan")
        transform = None
        robot_pose = None
        stamp = self._stamp_time(message)
        lookup_times = (stamp, Time()) if self.control_source == "NAV" else (stamp,)
        for lookup_time in lookup_times:
            try:
                transform = self.tf_buffer.lookup_transform(
                    "map", message.header.frame_id, lookup_time, Duration(seconds=0.05)
                ).transform
                break
            except Exception:
                continue
        if transform is not None:
            for base_frame in ("base_footprint", "base_link"):
                for lookup_time in lookup_times:
                    try:
                        base_transform = self.tf_buffer.lookup_transform(
                            "map", base_frame, lookup_time, Duration(seconds=0.05)
                        ).transform
                        yaw, yaw_source = self._visual_yaw(
                            self._yaw(base_transform.rotation.z, base_transform.rotation.w)
                        )
                        robot_pose = {
                            "x": float(base_transform.translation.x),
                            "y": float(base_transform.translation.y),
                            "yaw": yaw,
                            "yaw_source": yaw_source,
                        }
                        break
                    except Exception:
                        continue
                if robot_pose is not None:
                    break
        stride = max(1, len(message.ranges) // 240)
        points = []
        for index in range(0, len(message.ranges), stride):
            distance = float(message.ranges[index])
            if not math.isfinite(distance) or distance < message.range_min or distance > message.range_max:
                continue
            angle = message.angle_min + index * message.angle_increment
            x = distance * math.cos(angle)
            y = distance * math.sin(angle)
            if transform is not None:
                q = transform.rotation
                rotated_x, rotated_y = self._rotate_xy(x, y, q)
                x = rotated_x + transform.translation.x
                y = rotated_y + transform.translation.y
            points.append([x, y])
        frame = "map" if transform is not None else message.header.frame_id
        self.emitter.send(
            {
                "type": "scan",
                "points": points,
                "frame": frame,
                "origin": (
                    {
                        "x": float(transform.translation.x),
                        "y": float(transform.translation.y),
                        "yaw": self._yaw(transform.rotation.z, transform.rotation.w),
                    }
                    if transform is not None
                    else None
                ),
                "robot_pose": robot_pose,
                "localized": self.localization_initialized,
                "stamp": time.time(),
            }
        )

    def _path(self, message: Path) -> None:
        self._mark("path")
        points = [[float(item.pose.position.x), float(item.pose.position.y)] for item in message.poses]
        self.emitter.send({"type": "path", "points": points, "frame": message.header.frame_id, "stamp": time.time()})

    def _voltage(self, message: Float32) -> None:
        self.voltage = float(message.data)
        self._mark("voltage")

    def _arbiter_status(self, message: String) -> None:
        try:
            status = json.loads(message.data)
        except json.JSONDecodeError:
            status = {"raw": message.data}
        self.emitter.send({"type": "arbiter", **status, "stamp": time.time()})

    def _telemetry(self) -> None:
        now = time.monotonic()
        self.emitter.send(
            {
                "type": "telemetry",
                "voltage": self.voltage,
                "linear": self.linear,
                "angular": self.angular,
                "uptime": now - self.started,
                "freshness": {name: round(now - seen, 3) for name, seen in self.last_seen.items()},
                "stamp": time.time(),
            }
        )

    def _publish_zero(self) -> None:
        self.manual.publish(Twist())

    def _drive_watchdog(self) -> None:
        if self.last_drive and time.monotonic() - self.last_drive > 0.35:
            self.last_drive = 0.0
            self._publish_zero()

    def _pose(self, x: float, y: float, yaw: float) -> PoseStamped:
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose

    def _route_event(self, state: str, **extra: Any) -> None:
        self.emitter.send(
            {
                "type": "route",
                "state": state,
                "name": self.route_name,
                "index": self.route_index,
                "total": len(self.route_points),
                **extra,
                "stamp": time.time(),
            }
        )

    def _clear_route(self, state: str | None = None, **extra: Any) -> None:
        if state and self.route_points:
            self._route_event(state, **extra)
        self.route_token += 1
        self.route_name = ""
        self.route_points = []
        self.route_index = -1

    def _start_route(self, command: dict[str, Any]) -> None:
        points = command.get("points")
        if not isinstance(points, list) or not points:
            raise ValueError("Route requires at least one point")
        if self.goal_handle is not None:
            self.emitter.send({"type": "route", "state": "rejected", "detail": "Navigation is busy"})
            return
        self.route_token += 1
        self.route_name = str(command.get("name", "Route"))
        self.route_points = [dict(point) for point in points]
        self.route_index = 0
        token = self.route_token
        self._route_event("starting", point=self.route_points[0])
        self._send_goal(self.route_points[0], token, 0)

    def _send_goal(
        self,
        command: dict[str, Any],
        route_token: int | None = None,
        route_index: int | None = None,
    ) -> None:
        if not self.navigation.wait_for_server(timeout_sec=0.2):
            self.emitter.send({"type": "navigation", "state": "unavailable"})
            if route_token == self.route_token and self.route_points:
                self._clear_route("failed", detail="Navigation action is unavailable")
            return
        goal = NavigateToPose.Goal()
        goal.pose = self._pose(float(command["x"]), float(command["y"]), float(command.get("yaw", 0.0)))
        future = self.navigation.send_goal_async(
            goal,
            feedback_callback=lambda feedback: self._goal_feedback(
                feedback, route_token, route_index
            ),
        )
        future.add_done_callback(
            lambda result: self._goal_response(result, route_token, route_index)
        )

    def _goal_response(self, future, route_token: int | None, route_index: int | None) -> None:
        try:
            goal_handle = future.result()
        except Exception as exc:
            self.emitter.send({"type": "navigation", "state": "error", "detail": str(exc)})
            if route_token == self.route_token and self.route_points:
                self._clear_route("failed", detail=str(exc))
            return
        if route_token is not None and route_token != self.route_token:
            if goal_handle.accepted:
                goal_handle.cancel_goal_async()
            return
        self.goal_handle = goal_handle
        if not goal_handle.accepted:
            self.goal_handle = None
            self.emitter.send({"type": "navigation", "state": "rejected"})
            if route_token == self.route_token and self.route_points:
                self._clear_route("failed", detail="Waypoint was rejected")
            return
        self.emitter.send({"type": "navigation", "state": "accepted"})
        if route_token == self.route_token and self.route_points:
            self._route_event("running", point=self.route_points[self.route_index])
        result = goal_handle.get_result_async()
        result.add_done_callback(
            lambda value: self._goal_result(value, route_token, route_index)
        )

    def _goal_feedback(self, feedback, route_token: int | None, route_index: int | None) -> None:
        if route_token is not None and route_token != self.route_token:
            return
        remaining = feedback.feedback.distance_remaining
        self.emitter.send({"type": "navigation", "state": "running", "distance_remaining": float(remaining)})
        if route_token == self.route_token and self.route_points:
            self._route_event(
                "running",
                point=self.route_points[self.route_index],
                distance_remaining=float(remaining),
            )

    def _goal_result(self, future, route_token: int | None, route_index: int | None) -> None:
        try:
            status = int(future.result().status)
            self.goal_handle = None
            self.emitter.send({"type": "navigation", "state": "finished", "status": status})
        except Exception as exc:
            self.goal_handle = None
            self.emitter.send({"type": "navigation", "state": "error", "detail": str(exc)})
            if route_token == self.route_token and self.route_points:
                self._clear_route("failed", detail=str(exc))
            return
        if route_token is None or route_token != self.route_token or not self.route_points:
            return
        if status != GoalStatus.STATUS_SUCCEEDED:
            state = "canceled" if status == GoalStatus.STATUS_CANCELED else "failed"
            self._clear_route(state, status=status)
            return

        self._route_event(
            "waypoint_reached",
            point=self.route_points[self.route_index],
            completed=self.route_index + 1,
        )
        next_index = self.route_index + 1
        if next_index >= len(self.route_points):
            self._clear_route("finished", completed=len(self.route_points))
            return
        self.route_index = next_index
        self._route_event("running", point=self.route_points[next_index])
        self._send_goal(self.route_points[next_index], route_token, next_index)

    def _handle(self, command: dict[str, Any]) -> None:
        command_type = str(command.get("type", ""))
        if command_type == "drive":
            message = Twist()
            message.linear.x = max(-0.35, min(0.35, float(command.get("linear", 0.0))))
            message.angular.z = max(-1.2, min(1.2, float(command.get("angular", 0.0))))
            self.manual.publish(message)
            self.last_drive = time.monotonic()
        elif command_type == "set_control_mode":
            message = String()
            source = str(command.get("source", "IDLE")).upper()
            if source == "NAV" and self.control_source != "NAV":
                self.localization_initialized = False
                self.localization_pending = False
                self._localization_event("waiting_initial_pose")
            if source != self.control_source:
                self.imu_map_offset = None
            self.control_source = source
            message.data = source
            self.control_mode.publish(message)
        elif command_type == "emergency_stop":
            self._publish_zero()
            message = Bool()
            message.data = True
            self.emergency.publish(message)
            goal_handle = self.goal_handle
            self._clear_route("canceled", detail="Emergency stop")
            if goal_handle:
                goal_handle.cancel_goal_async()
        elif command_type == "clear_emergency":
            message = Bool()
            message.data = False
            self.emergency.publish(message)
        elif command_type == "initial_pose":
            pose = PoseWithCovarianceStamped()
            stamped = self._pose(float(command["x"]), float(command["y"]), float(command.get("yaw", 0.0)))
            pose.header = stamped.header
            pose.pose.pose = stamped.pose
            pose.pose.covariance[0] = 0.25
            pose.pose.covariance[7] = 0.25
            pose.pose.covariance[35] = 0.0685
            self.localization_initialized = False
            self.localization_pending = True
            self.localization_requested_at = time.monotonic()
            self.localization_update_requested_at = 0.0
            self.initial_pose.publish(pose)
            self._localization_event(
                "adjusting",
                requested={
                    "x": float(command["x"]),
                    "y": float(command["y"]),
                    "yaw": float(command.get("yaw", 0.0)),
                },
            )
        elif command_type == "goal":
            self._send_goal(command)
        elif command_type == "route_start":
            self._start_route(command)
        elif command_type == "cancel_goal":
            goal_handle = self.goal_handle
            self._clear_route("canceled", detail="Canceled by operator")
            if goal_handle:
                goal_handle.cancel_goal_async()
        elif command_type == "shutdown":
            self.fail_safe()
            rclpy.shutdown()

    def _drain_commands(self) -> None:
        for _ in range(16):
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle(command)
            except (KeyError, TypeError, ValueError) as exc:
                self.emitter.send({"type": "command_error", "detail": str(exc)})

    def fail_safe(self) -> None:
        self._publish_zero()
        goal_handle = self.goal_handle
        self._clear_route("canceled", detail="Bridge shutdown")
        if goal_handle:
            goal_handle.cancel_goal_async()
        emergency = Bool()
        emergency.data = True
        self.emergency.publish(emergency)


def read_commands(commands: queue.Queue[dict[str, Any]], emitter: JsonEmitter) -> None:
    for line in sys.stdin:
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            emitter.send({"type": "command_error", "detail": "invalid JSON"})
            continue
        if isinstance(command, dict):
            commands.put(command)
    commands.put({"type": "shutdown"})


def main() -> None:
    commands: queue.Queue[dict[str, Any]] = queue.Queue()
    emitter = JsonEmitter()
    rclpy.init()
    node = WebRosBridge(emitter, commands)
    reader = threading.Thread(target=read_commands, args=(commands, emitter), daemon=True)
    reader.start()
    emitter.send({"type": "bridge_ready", "stamp": time.time()})
    try:
        rclpy.spin(node)
    finally:
        node.fail_safe()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
