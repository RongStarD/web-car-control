from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .bridge import DemoBridge, RosContainerBridge
from .config import load_settings
from .events import EventHub
from .service import ControlService
from .supervisor import ContainerSupervisor, DemoSupervisor

SERVICE_KEY: web.AppKey[ControlService] = web.AppKey("service", ControlService)
EVENTS_KEY: web.AppKey[EventHub] = web.AppKey("events", EventHub)


@web.middleware
async def json_errors(request: web.Request, handler):
    try:
        return await handler(request)
    except (ValueError, RuntimeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except OSError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def bootstrap(request: web.Request) -> web.Response:
    return web.json_response(request.app[SERVICE_KEY].bootstrap())


async def preflight(request: web.Request) -> web.Response:
    return web.json_response(request.app[SERVICE_KEY].preflight())


async def set_feature(request: web.Request) -> web.Response:
    body = await request.json()
    state = await request.app[SERVICE_KEY].set_feature(str(body.get("feature", "")))
    return web.json_response({"ok": True, "accepted": True, "runtime": state}, status=202)


async def stop_system(request: web.Request) -> web.Response:
    state = await request.app[SERVICE_KEY].stop()
    return web.json_response({"ok": True, "accepted": True, "runtime": state}, status=202)


async def command(request: web.Request) -> web.Response:
    body = await request.json()
    await request.app[SERVICE_KEY].command(body)
    return web.json_response({"ok": True})


async def save_map(request: web.Request) -> web.Response:
    body = await request.json()
    result = await request.app[SERVICE_KEY].save_map(str(body.get("name", "")), body)
    return web.json_response({"ok": True, **result})


async def list_maps(request: web.Request) -> web.Response:
    return web.json_response(request.app[SERVICE_KEY].map_profiles())


async def update_map(request: web.Request) -> web.Response:
    body = await request.json()
    result = request.app[SERVICE_KEY].update_map(request.match_info["name"], body)
    return web.json_response({"ok": True, **result})


async def activate_map(request: web.Request) -> web.Response:
    result = await request.app[SERVICE_KEY].activate_map(request.match_info["name"])
    return web.json_response({"ok": True, **result})


async def component_log(request: web.Request) -> web.Response:
    lines = int(request.query.get("lines", "100"))
    output = await request.app[SERVICE_KEY].component_log(request.match_info["component"], lines)
    return web.json_response({"component": request.match_info["component"], "log": output})


async def websocket(request: web.Request) -> web.WebSocketResponse:
    service = request.app[SERVICE_KEY]
    events = request.app[EVENTS_KEY]
    socket = web.WebSocketResponse(heartbeat=15, max_msg_size=1_048_576)
    await socket.prepare(request)
    queue = events.subscribe()
    await service.client_connected()
    await socket.send_json({"type": "bootstrap", **service.bootstrap()})

    async def writer() -> None:
        while True:
            await socket.send_json(await queue.get())

    writer_task = asyncio.create_task(writer())
    try:
        async for message in socket:
            if message.type == WSMsgType.TEXT:
                try:
                    payload = json.loads(message.data)
                    if isinstance(payload, dict) and payload.get("type") == "heartbeat":
                        await socket.send_json({"type": "heartbeat", "ok": True})
                except json.JSONDecodeError:
                    await socket.send_json({"type": "error", "message": "Invalid JSON"})
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        writer_task.cancel()
        events.unsubscribe(queue)
        await service.client_disconnected()
    return socket


async def root_fallback(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "service": "iCar Web Console",
            "status": "backend ready",
            "frontend": "Build web/frontend to enable the browser interface",
        }
    )


def create_app(config_path: Path, demo: bool = False, frontend_dist: Path | None = None) -> web.Application:
    settings = load_settings(config_path)
    events = EventHub()
    supervisor = DemoSupervisor(settings) if demo else ContainerSupervisor(settings)
    bridge = DemoBridge(events.publish) if demo else RosContainerBridge(settings, events.publish)
    service = ControlService(settings, supervisor, bridge, events, demo=demo)

    app = web.Application(middlewares=[json_errors])
    app[SERVICE_KEY] = service
    app[EVENTS_KEY] = events
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/preflight", preflight)
    app.router.add_post("/api/system/feature", set_feature)
    app.router.add_post("/api/system/stop", stop_system)
    app.router.add_post("/api/command", command)
    app.router.add_post("/api/maps/save", save_map)
    app.router.add_get("/api/maps", list_maps)
    app.router.add_post("/api/maps/{name}", update_map)
    app.router.add_post("/api/maps/{name}/activate", activate_map)
    app.router.add_get("/api/components/{component}/log", component_log)
    app.router.add_get("/ws", websocket)

    if frontend_dist and frontend_dist.is_dir():
        app.router.add_get("/", lambda request: web.FileResponse(frontend_dist / "index.html"))
        assets = frontend_dist / "assets"
        if assets.is_dir():
            app.router.add_static("/assets", assets)
        app.router.add_get("/{tail:.*}", lambda request: web.FileResponse(frontend_dist / "index.html"))
    else:
        app.router.add_get("/", root_fallback)

    async def startup(_: web.Application) -> None:
        await service.start()

    async def cleanup(_: web.Application) -> None:
        await service.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="iCar Web process orchestrator")
    default_config = Path(__file__).resolve().parents[2] / "config" / "system.json"
    default_frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    parser.add_argument("--config", type=Path, default=Path(os.getenv("ICAR_CONFIG", default_config)))
    parser.add_argument("--frontend", type=Path, default=default_frontend)
    parser.add_argument("--demo", action="store_true", default=os.getenv("ICAR_DEMO") == "1")
    arguments = parser.parse_args()
    settings = load_settings(arguments.config)
    web.run_app(
        create_app(arguments.config, demo=arguments.demo, frontend_dist=arguments.frontend),
        host=settings.host,
        port=int(os.getenv("ICAR_PORT", settings.port)),
        shutdown_timeout=5.0,
    )


if __name__ == "__main__":
    main()
