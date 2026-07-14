from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from .command import CommandResult, run_command
from .models import ProcessState, Settings, TargetState


class Supervisor(Protocol):
    async def ensure_target(self, target: str) -> TargetState: ...

    async def stop_target(self, target: str) -> TargetState: ...

    async def target_status(self, target: str) -> TargetState: ...

    async def hardware_status(self) -> dict[str, dict[str, object]]: ...

    async def start(self, component: str) -> ProcessState: ...

    async def stop(self, component: str) -> ProcessState: ...

    async def status(self, component: str) -> ProcessState: ...

    async def run_once(self, target: str, name: str, command: str, timeout: float) -> CommandResult: ...

    async def nodes(self, target: str) -> list[str]: ...

    async def lifecycle(self, target: str, node: str) -> str: ...

    async def lifecycle_set(self, target: str, node: str, transition: str) -> str: ...

    async def tail(self, component: str, lines: int = 80) -> str: ...


class ContainerSupervisor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _run(self, command: list[str], timeout: float | None = None) -> CommandResult:
        return await run_command(
            command,
            timeout or self.settings.runtime.command_timeout_seconds,
        )

    def _docker(self, *arguments: str) -> list[str]:
        return [self.settings.runtime.docker_binary, *arguments]

    def _runner(self, target: str, action: str, name: str = "system", *extra: str) -> list[str]:
        target_config = self.settings.targets[target]
        return self._docker(
            "exec",
            target_config.container,
            target_config.runner_path,
            action,
            name,
            *extra,
        )

    async def target_status(self, target: str) -> TargetState:
        config = self.settings.targets[target]
        result = await self._run(
            self._docker("inspect", "-f", "{{.State.Running}}", config.container),
            timeout=5,
        )
        running = result.returncode == 0 and result.stdout.strip().lower() == "true"
        detail = result.stdout.strip() if result.returncode == 0 else (result.stderr or result.stdout).strip()
        return TargetState(target, config.container, running, detail)

    async def hardware_status(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for name, config in self.settings.hardware.items():
            path = Path(config.host_path)
            present = path.exists()
            detail = "present"
            if present and path.is_symlink():
                try:
                    detail = f"present -> {path.resolve()}"
                except OSError:
                    detail = "present (unresolved symlink)"
            elif not present:
                detail = "not detected"
            result[name] = {
                "label": config.label,
                "path": config.host_path,
                "present": present,
                "required_for_motion": config.required_for_motion,
                "detail": detail,
            }
        return result

    async def ensure_target(self, target: str) -> TargetState:
        state = await self.target_status(target)
        if state.running:
            return state
        result = await self._run(self._docker("start", state.container), timeout=25)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f"Failed to start {state.container}")
        return await self.target_status(target)

    async def stop_target(self, target: str) -> TargetState:
        state = await self.target_status(target)
        if not state.running:
            return state
        result = await self._run(self._docker("stop", "-t", "8", state.container), timeout=14)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f"Failed to stop {state.container}")
        return await self.target_status(target)

    @staticmethod
    def _state(name: str, target: str, result: CommandResult) -> ProcessState:
        output = result.stdout.strip()
        if result.returncode != 0:
            return ProcessState(name=name, target=target, running=False, detail=result.stderr or output)
        words = output.split()
        if words and words[0] in {"running", "started"}:
            pid = int(words[1]) if len(words) > 1 and words[1].isdigit() else None
            return ProcessState(name=name, target=target, running=True, pid=pid, detail=output)
        exit_code = None
        if len(words) > 1 and words[0] == "exited" and words[1].lstrip("-").isdigit():
            exit_code = int(words[1])
        return ProcessState(
            name=name,
            target=target,
            running=False,
            detail=output or result.stderr,
            exit_code=exit_code,
        )

    async def start(self, component: str) -> ProcessState:
        config = self.settings.components[component]
        result = await self._run(self._runner(config.target, "start", component, config.command))
        state = self._state(component, config.target, result)
        if result.returncode != 0 or not state.running:
            raise RuntimeError(state.detail or f"Failed to start {component}")
        return state

    async def stop(self, component: str) -> ProcessState:
        config = self.settings.components[component]
        target_state = await self.target_status(config.target)
        if not target_state.running:
            return ProcessState(component, config.target, False, detail="container stopped")
        result = await self._run(self._runner(config.target, "stop", component), timeout=10)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f"Failed to stop {component}")
        return ProcessState(component, config.target, False, detail=result.stdout.strip())

    async def status(self, component: str) -> ProcessState:
        config = self.settings.components[component]
        target_state = await self.target_status(config.target)
        if not target_state.running:
            return ProcessState(component, config.target, False, detail="container stopped")
        result = await self._run(self._runner(config.target, "status", component), timeout=5)
        return self._state(component, config.target, result)

    async def run_once(self, target: str, name: str, command: str, timeout: float) -> CommandResult:
        result = await self._run(
            self._runner(target, "run-once", name, str(max(1, int(timeout))), command),
            timeout=timeout + 6,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f"Failed to run {name}")
        return result

    async def nodes(self, target: str) -> list[str]:
        result = await self._run(self._runner(target, "nodes"), timeout=6)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or "Unable to inspect ROS node graph")
        return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})

    async def lifecycle(self, target: str, node: str) -> str:
        result = await self._run(self._runner(target, "lifecycle", "system", node), timeout=10)
        if result.returncode != 0:
            return "unavailable"
        output = result.stdout.lower()
        # Check longer states first: "active" is a substring of "inactive".
        for state in ("unconfigured", "inactive", "active", "finalized"):
            if state in output:
                return state
        return output.strip() or "unknown"

    async def lifecycle_set(self, target: str, node: str, transition: str) -> str:
        result = await self._run(
            self._runner(target, "lifecycle-set", "system", node, transition),
            timeout=20,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f"Lifecycle transition failed for {node}")
        return result.stdout.strip()

    async def tail(self, component: str, lines: int = 80) -> str:
        config = self.settings.components[component]
        result = await self._run(
            self._runner(config.target, "tail", component, str(lines)),
            timeout=6,
        )
        return result.stdout or result.stderr


class DemoSupervisor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._states: dict[str, ProcessState] = {}
        self._targets: set[str] = set()
        self._pid = 4200

    async def ensure_target(self, target: str) -> TargetState:
        self._targets.add(target)
        config = self.settings.targets[target]
        return TargetState(target, config.container, True, "demo running")

    async def stop_target(self, target: str) -> TargetState:
        self._targets.discard(target)
        config = self.settings.targets[target]
        return TargetState(target, config.container, False, "demo stopped")

    async def target_status(self, target: str) -> TargetState:
        config = self.settings.targets[target]
        return TargetState(target, config.container, target in self._targets, "demo")

    async def hardware_status(self) -> dict[str, dict[str, object]]:
        return {
            name: {
                "label": config.label,
                "path": config.host_path,
                "present": True,
                "required_for_motion": config.required_for_motion,
                "detail": "simulated",
            }
            for name, config in self.settings.hardware.items()
        }

    async def start(self, component: str) -> ProcessState:
        await asyncio.sleep(0.02)
        config = self.settings.components[component]
        self._targets.add(config.target)
        self._pid += 1
        state = ProcessState(component, config.target, True, self._pid, f"demo: {config.command}")
        self._states[component] = state
        return state

    async def stop(self, component: str) -> ProcessState:
        await asyncio.sleep(0.01)
        config = self.settings.components[component]
        state = ProcessState(component, config.target, False, detail="demo stopped")
        self._states[component] = state
        return state

    async def status(self, component: str) -> ProcessState:
        config = self.settings.components[component]
        return self._states.get(component, ProcessState(component, config.target, False))

    async def run_once(self, target: str, name: str, command: str, timeout: float) -> CommandResult:
        await asyncio.sleep(0.05)
        return CommandResult(0, f"demo completed: {command}", "")

    async def nodes(self, target: str) -> list[str]:
        return sorted(
            node
            for name, state in self._states.items()
            if state.running and state.target == target
            for node in self.settings.components[name].nodes
        )

    async def lifecycle(self, target: str, node: str) -> str:
        return "active"

    async def lifecycle_set(self, target: str, node: str, transition: str) -> str:
        return f"demo {node}: {transition}"

    async def tail(self, component: str, lines: int = 80) -> str:
        state = await self.status(component)
        return f"[demo] {component}: {'running' if state.running else 'stopped'}"
