from __future__ import annotations

import asyncio
import json
import math
import shlex
import time
from typing import Any, Awaitable, Callable, Dict, Protocol

from .models import Settings

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class Bridge(Protocol):
    available: bool
    target: str | None

    async def start(self, target: str) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, command: dict[str, Any]) -> None: ...

    def summary(self) -> dict[str, Any]: ...

    def topic_freshness(self) -> dict[str, float | None]: ...


class RosContainerBridge:
    def __init__(self, settings: Settings, on_event: EventCallback) -> None:
        self.settings = settings
        self.on_event = on_event
        self.available = False
        self.target: str | None = None
        self.detail = "not started"
        self._process: asyncio.subprocess.Process | None = None
        self._reader_tasks: list[asyncio.Task[None]] = []
        self._event_times: dict[str, float] = {}
        self._ready_event: asyncio.Event | None = None

    async def start(self, target: str) -> None:
        if self.available and self.target == target:
            return
        await self.stop()
        config = self.settings.targets[target]
        command = [
            self.settings.runtime.docker_binary,
            "exec",
            "-i",
            config.container,
            "bash",
            "-lic",
            f"exec python3 -u {shlex.quote(config.bridge_path)}",
        ]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=8 * 1024 * 1024,
            )
        except OSError as exc:
            self.detail = str(exc)
            await self.on_event({"type": "bridge", "available": False, "detail": self.detail})
            raise RuntimeError(self.detail) from exc

        self.target = target
        self.available = False
        self.detail = f"starting bridge in {config.container}"
        self._event_times.clear()
        self._ready_event = asyncio.Event()
        self._reader_tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
            asyncio.create_task(self._watch_exit()),
        ]
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=8)
        except TimeoutError as exc:
            detail = self.detail
            await self.stop()
            raise RuntimeError(f"ROS bridge did not become ready: {detail}") from exc
        self.available = True
        self.detail = f"connected to {config.container}"
        await self.on_event({"type": "bridge", "available": True, "target": target, "detail": self.detail})

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        while line := await self._process.stdout.readline():
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict):
                event_type = str(event.get("type", "event"))
                self._event_times[event_type] = time.monotonic()
                if event_type == "bridge_ready" and self._ready_event:
                    self._ready_event.set()
                await self.on_event(event)

    async def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        shell_noise = {
            "bash: cannot set terminal process group (-1): Inappropriate ioctl for device",
            "bash: no job control in this shell",
        }
        while line := await self._process.stderr.readline():
            detail = line.decode("utf-8", errors="replace").strip()
            if detail and detail not in shell_noise:
                self.detail = detail
                await self.on_event({"type": "log", "level": "WARN", "message": detail})

    async def _watch_exit(self) -> None:
        assert self._process
        returncode = await self._process.wait()
        self.available = False
        self.detail = f"ROS bridge exited with code {returncode}"
        await self.on_event({"type": "bridge", "available": False, "detail": self.detail})

    async def send(self, command: dict[str, Any]) -> None:
        if not self.available or not self._process or not self._process.stdin:
            raise RuntimeError("ROS bridge is not available")
        payload = json.dumps(command, separators=(",", ":")) + "\n"
        self._process.stdin.write(payload.encode("utf-8"))
        await self._process.stdin.drain()

    async def stop(self) -> None:
        process = self._process
        if process and process.returncode is None:
            try:
                if process.stdin:
                    payload = json.dumps({"type": "shutdown"}) + "\n"
                    process.stdin.write(payload.encode("utf-8"))
                    await process.stdin.drain()
                await asyncio.wait_for(process.wait(), timeout=3)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, asyncio.TimeoutError):
                if process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    await process.wait()
        for task in self._reader_tasks:
            task.cancel()
        self._reader_tasks.clear()
        self._process = None
        self._ready_event = None
        self.available = False
        self.target = None
        self.detail = "stopped"

    def summary(self) -> dict[str, Any]:
        return {"available": self.available, "target": self.target, "detail": self.detail}

    def topic_freshness(self) -> dict[str, float | None]:
        now = time.monotonic()
        names = {
            "map": "/map",
            "pose": "/odom",
            "scan": "/scan",
            "local_costmap": "/local_costmap/costmap",
            "global_costmap": "/global_costmap/costmap",
        }
        return {
            topic: (now - self._event_times[event_type] if event_type in self._event_times else None)
            for event_type, topic in names.items()
        }


class DemoBridge:
    def __init__(self, on_event: EventCallback) -> None:
        self.on_event = on_event
        self.available = False
        self.target: str | None = None
        self.detail = "demo stopped"
        self._task: asyncio.Task[None] | None = None
        self._goal: tuple[float, float] | None = (2.8, 1.6)
        self._drive = (0.0, 0.0)
        self._localized = False
        self._route_active = False
        self._event_times: dict[str, float] = {}
        self._started = 0.0

    @staticmethod
    def _rle(values: list[int]) -> list[list[int]]:
        result: list[list[int]] = []
        for value in values:
            if result and result[-1][0] == value:
                result[-1][1] += 1
            else:
                result.append([value, 1])
        return result

    def _grid_event(self, event_type: str, width: int, height: int, resolution: float) -> dict[str, Any]:
        cells = [0] * (width * height)
        for y in range(height):
            for x in range(width):
                border = x < 3 or y < 3 or x >= width - 3 or y >= height - 3
                wall = (58 <= x < 62 and y < 90) or (76 <= y < 80 and x > 96)
                island = 112 < x < 137 and 25 < y < 45
                cells[y * width + x] = 100 if border or wall or island else 0
        if event_type == "map":
            for y in range(8, 18):
                for x in range(148, 170):
                    cells[y * width + x] = -1
        else:
            for y in range(max(0, height // 2 - 8), min(height, height // 2 + 9)):
                for x in range(max(0, width // 2 - 8), min(width, width // 2 + 9)):
                    distance = math.hypot(x - width / 2, y - height / 2)
                    cells[y * width + x] = max(cells[y * width + x], int(max(0, 100 - distance * 8)))
        return {
            "type": event_type,
            "width": width,
            "height": height,
            "resolution": resolution,
            "origin": {"x": -4.5 if event_type == "map" else -2.0, "y": -3.2 if event_type == "map" else -2.0, "yaw": 0.0},
            "data_rle": self._rle(cells),
            "stamp": time.time(),
        }

    async def _emit(self, event: dict[str, Any]) -> None:
        self._event_times[str(event["type"])] = time.monotonic()
        await self.on_event(event)

    async def start(self, target: str) -> None:
        if self.available and self.target == target:
            return
        await self.stop()
        self.available = True
        self.target = target
        self.detail = "demo data source"
        self._started = time.monotonic()
        await self._emit({"type": "bridge", "available": True, "target": target, "detail": self.detail})
        await self._emit(self._grid_event("map", 180, 128, 0.05))
        await self._emit(self._grid_event("global_costmap", 180, 128, 0.05))
        await self._emit(self._grid_event("local_costmap", 80, 80, 0.05))
        self._task = asyncio.create_task(self._animate())

    async def _animate(self) -> None:
        while True:
            elapsed = time.monotonic() - self._started
            x = -1.4 + math.cos(elapsed * 0.18) * 1.2
            y = -0.4 + math.sin(elapsed * 0.18) * 0.8
            yaw = elapsed * 0.18 + math.pi / 2
            await self._emit({"type": "pose", "x": x, "y": y, "yaw": yaw, "localized": self._localized, "stamp": time.time()})
            scan = []
            for index in range(96):
                angle = index / 96 * math.tau
                radius = 1.1 + 0.18 * math.sin(angle * 5 + elapsed)
                scan.append([radius * math.cos(angle), radius * math.sin(angle)])
            await self._emit({"type": "scan", "points": scan, "frame": "base", "stamp": time.time()})
            if self._goal:
                await self._emit(
                    {
                        "type": "path",
                        "points": [[x, y], [0.2, 0.4], [1.5, 1.0], [self._goal[0], self._goal[1]]],
                        "stamp": time.time(),
                    }
                )
            await self._emit(
                {
                    "type": "telemetry",
                    "voltage": 11.9 + math.sin(elapsed * 0.1) * 0.08,
                    "linear": self._drive[0],
                    "angular": self._drive[1],
                    "uptime": elapsed,
                    "stamp": time.time(),
                }
            )
            await asyncio.sleep(0.25)

    async def send(self, command: dict[str, Any]) -> None:
        command_type = command.get("type")
        if command_type == "goal":
            self._goal = (float(command["x"]), float(command["y"]))
            await self._emit({"type": "navigation", "state": "accepted"})
        elif command_type in {"cancel_goal", "emergency_stop"}:
            self._goal = None
            self._drive = (0.0, 0.0)
            await self._emit({"type": "navigation", "state": "canceled"})
            if self._route_active:
                self._route_active = False
                await self._emit({"type": "route", "state": "canceled", "stamp": time.time()})
        elif command_type == "drive":
            self._drive = (float(command["linear"]), float(command["angular"]));
        elif command_type == "set_control_mode":
            if str(command.get("source", "IDLE")).upper() == "NAV":
                self._localized = False
                await self._emit({"type": "localization", "state": "waiting_initial_pose", "localized": False})
            await self._emit({"type": "control", "source": command.get("source", "IDLE")})
        elif command_type == "initial_pose":
            await self._emit({"type": "localization", "state": "adjusting", "localized": False})
            await asyncio.sleep(0.2)
            self._localized = True
            await self._emit({"type": "localization", "state": "ready", "localized": True})
        elif command_type == "route_start":
            points = command.get("points", [])
            self._route_active = True
            await self._emit(
                {
                    "type": "route",
                    "state": "running",
                    "name": command.get("name", "Route"),
                    "index": 0,
                    "total": len(points),
                    "stamp": time.time(),
                }
            )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.available = False
        self.target = None
        self.detail = "demo stopped"

    def summary(self) -> dict[str, Any]:
        return {"available": self.available, "target": self.target, "detail": self.detail}

    def topic_freshness(self) -> dict[str, float | None]:
        now = time.monotonic()
        return {
            topic: (now - self._event_times[event_type] if event_type in self._event_times else None)
            for event_type, topic in {
                "map": "/map",
                "pose": "/odom",
                "scan": "/scan",
                "local_costmap": "/local_costmap/costmap",
                "global_costmap": "/global_costmap/costmap",
            }.items()
        }
