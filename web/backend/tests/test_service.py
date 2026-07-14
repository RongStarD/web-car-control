from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from icar_web.bridge import DemoBridge
from icar_web.config import load_settings
from icar_web.events import EventHub
from icar_web.models import Phase
from icar_web.service import ControlService
from icar_web.supervisor import DemoSupervisor


CONFIG = Path(__file__).resolve().parents[2] / "config" / "system.json"


class ControlServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_feature_transition_is_accepted_without_waiting(self) -> None:
        loaded = load_settings(CONFIG)
        settings = replace(
            loaded,
            runtime=replace(loaded.runtime, start_settle_seconds=0.2, readiness_grace_seconds=0.1),
        )
        events = EventHub()
        service = ControlService(
            settings,
            DemoSupervisor(settings),
            DemoBridge(events.publish),
            events,
            demo=True,
        )
        await service.start()
        try:
            runtime = await service.set_feature("WEB_MANUAL")
            self.assertEqual(runtime["phase"], Phase.STARTING.value)
            with self.assertRaises(RuntimeError):
                await service.set_feature("SLAM")
            for _ in range(30):
                if service._operation_task is None:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(service.manager.state.phase, Phase.READY)
        finally:
            await service.close()

    async def test_route_command_resolves_only_saved_waypoints(self) -> None:
        loaded = load_settings(CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                loaded,
                map_save=replace(loaded.map_save, host_directory=directory),
            )
            bridge = AsyncMock()
            bridge.available = True
            events = EventHub()
            service = ControlService(
                settings,
                DemoSupervisor(settings),
                bridge,
                events,
                demo=True,
            )
            service.maps.save_new_map(
                "floor_8",
                {
                    "waypoints": [
                        {"id": "kitchen", "name": "Kitchen", "x": 1, "y": 2, "yaw": 0},
                        {"id": "room_808", "name": "Room 808", "x": 5, "y": 7, "yaw": 1.5},
                    ],
                    "default_pose_id": "kitchen",
                    "routes": [
                        {
                            "id": "delivery",
                            "name": "Delivery",
                            "waypoint_ids": ["kitchen", "room_808", "kitchen"],
                        }
                    ],
                },
            )
            service.maps.set_active("floor_8")
            service.manager.state.feature = "TASK_ROUTE"
            service.manager.state.phase = Phase.READY
            events.latest["pose"] = {"type": "pose", "localized": True}

            await service.command(
                {"type": "route_start", "map_name": "floor_8", "route_id": "delivery"}
            )
            route_command = bridge.send.await_args.args[0]
            self.assertEqual(route_command["type"], "route_start")
            self.assertEqual(
                [point["id"] for point in route_command["points"]],
                ["kitchen", "room_808", "kitchen"],
            )

            bridge.send.reset_mock()
            await service.command({"type": "map_initial_pose", "map_name": "floor_8"})
            initial_pose = bridge.send.await_args.args[0]
            self.assertEqual(initial_pose["type"], "initial_pose")
            self.assertEqual(initial_pose["id"], "kitchen")
