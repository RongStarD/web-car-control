from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from icar_web.bridge import RosContainerBridge
from icar_web.config import load_settings


CONFIG = Path(__file__).resolve().parents[2] / "config" / "system.json"


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_container_bridge_starts_in_ros_login_environment(self) -> None:
        bridge = RosContainerBridge(load_settings(CONFIG), AsyncMock())
        with patch(
            "icar_web.bridge.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=OSError("test stop"),
        ) as spawn:
            with self.assertRaises(RuntimeError):
                await bridge.start("yahboom_nav")

        command = spawn.await_args.args
        self.assertEqual(command[:6], ("docker", "exec", "-i", "nifty_dirac", "bash", "-lic"))
        self.assertIn("exec python3 -u /opt/icar-web/ros/icar_ros_bridge.py", command[6])
        self.assertEqual(spawn.await_args.kwargs["limit"], 8 * 1024 * 1024)

    async def test_container_bridge_stop_tolerates_asyncio_timeout(self) -> None:
        bridge = RosContainerBridge(load_settings(CONFIG), AsyncMock())
        process = MagicMock()
        process.returncode = None
        process.stdin = None
        process.wait = AsyncMock(return_value=0)
        process.terminate = MagicMock(side_effect=lambda: setattr(process, "returncode", 0))
        bridge._process = process
        bridge.available = True
        bridge.target = "yahboom_nav"

        attempts = 0

        async def wait_for(awaitable, timeout):
            nonlocal attempts
            attempts += 1
            awaitable.close()
            if attempts == 1:
                raise asyncio.TimeoutError()
            return 0

        with patch("icar_web.bridge.asyncio.wait_for", new=wait_for):
            await bridge.stop()

        process.terminate.assert_called_once_with()
        self.assertFalse(bridge.available)
        self.assertIsNone(bridge.target)
        self.assertEqual(bridge.detail, "stopped")
