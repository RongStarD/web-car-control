from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from icar_web.config import feature_components, load_settings
from icar_web.manager import SystemOrchestrator
from icar_web.models import Phase
from icar_web.supervisor import DemoSupervisor


CONFIG = Path(__file__).resolve().parents[2] / "config" / "system.json"


class FailingSupervisor(DemoSupervisor):
    def __init__(self, settings, failing_component: str) -> None:
        super().__init__(settings)
        self.failing_component = failing_component
        self.failed = False

    async def start(self, component: str):
        if component == self.failing_component and not self.failed:
            self.failed = True
            raise RuntimeError("injected start failure")
        return await super().start(component)


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        loaded = load_settings(CONFIG)
        self.settings = replace(
            loaded,
            runtime=replace(loaded.runtime, start_settle_seconds=0.0, readiness_grace_seconds=0.2),
        )
        self.supervisor = DemoSupervisor(self.settings)
        self.events = []

        async def publish(event):
            self.events.append(event)

        self.manager = SystemOrchestrator(self.settings, self.supervisor, publish)
        await self.manager.recover()

    async def test_switch_reuses_shared_components(self) -> None:
        await self.manager.set_feature("WEB_MANUAL")
        manual_pids = {
            component: (await self.supervisor.status(component)).pid
            for component in self.manager.state.active_components
        }
        await self.manager.set_feature("SLAM")
        self.assertEqual(self.manager.state.phase, Phase.READY)
        self.assertEqual(self.manager.state.active_components, feature_components(self.settings, "SLAM"))
        self.assertEqual(
            manual_pids["nav_driver"],
            (await self.supervisor.status("nav_driver")).pid,
        )
        self.assertTrue((await self.supervisor.status("nav_slam_gmapping")).running)

    async def test_cross_container_switch_stops_previous_target(self) -> None:
        await self.manager.set_feature("NAV_DWA")
        await self.manager.set_feature("LASER_AVOID")
        self.assertFalse((await self.supervisor.target_status("yahboom_nav")).running)
        self.assertTrue((await self.supervisor.target_status("icar_behavior")).running)
        self.assertEqual(
            self.manager.state.active_components,
            feature_components(self.settings, "LASER_AVOID"),
        )

    async def test_failed_switch_restores_previous_feature(self) -> None:
        supervisor = FailingSupervisor(self.settings, "nav2_controller_dwa")

        async def publish(_event):
            pass

        manager = SystemOrchestrator(self.settings, supervisor, publish)
        await manager.recover()
        await manager.set_feature("WEB_MANUAL")

        with self.assertRaisesRegex(RuntimeError, "previous feature restored"):
            await manager.set_feature("NAV_DWA")

        self.assertEqual(manager.state.feature, "WEB_MANUAL")
        self.assertEqual(manager.state.phase, Phase.READY)
        self.assertEqual(
            manager.state.active_components,
            feature_components(self.settings, "WEB_MANUAL"),
        )
        for component in manager.state.active_components:
            self.assertTrue((await supervisor.status(component)).running)

    async def test_disabled_feature_cannot_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "video0"):
            await self.manager.set_feature("VISUAL_TRACK")

    async def test_stop_clears_managed_processes(self) -> None:
        await self.manager.set_feature("WEB_MANUAL")
        await self.manager.stop_all()
        self.assertEqual(self.manager.state.feature, "IDLE")
        self.assertEqual(self.manager.state.phase, Phase.IDLE)
        self.assertEqual(self.manager.state.active_components, [])

    async def test_recovery_never_auto_resumes_motion(self) -> None:
        for component in feature_components(self.settings, "WEB_MANUAL"):
            await self.supervisor.start(component)
        recovered = SystemOrchestrator(self.settings, self.supervisor, self.manager.on_state)
        await recovered.recover()
        self.assertEqual(recovered.state.feature, "WEB_MANUAL")
        self.assertEqual(recovered.state.phase, Phase.ERROR)
        self.assertIn("operator", recovered.state.message)


if __name__ == "__main__":
    unittest.main()
