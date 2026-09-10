
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect

from tools.moonraker_simulator.state import SimulatorState

app = FastAPI(title="QIDI Moonraker Simulator")

state = SimulatorState()

_SIMULATED_MOONRAKER_VERSION = "v0.9.3-simulated"
_SIMULATED_API_VERSION = [1, 0, 0]
_SIMULATED_API_VERSION_STRING = "1.0.0"
_SIMULATED_KLIPPER_VERSION = "v0.12.0-simulated"
_SIMULATED_HOSTNAME = "qidi-moonraker-simulator"
_SIMULATED_MOONRAKER_CONFIG_FILE = "/home/pi/printer_data/config/moonraker.conf"
_SIMULATED_KLIPPER_CONFIG_FILE = "/home/pi/printer_data/config/printer.cfg"

@app.get("/server/info")
def server_info() -> dict:
    return {
        "result": {
            "klippy_connected": state.webhooks_state == "ready",
            "klippy_state": state.webhooks_state,
            "components": ["klippy_connection", "file_manager", "job_queue"],
            "failed_components": [],
            "registered_directories": ["gcodes", "config", "logs"],
            "warnings": [],
            "websocket_count": 0,
            "moonraker_version": _SIMULATED_MOONRAKER_VERSION,
            "api_version": _SIMULATED_API_VERSION,
            "api_version_string": _SIMULATED_API_VERSION_STRING,
            "config_file": _SIMULATED_MOONRAKER_CONFIG_FILE,
        }
    }

@app.get("/printer/info")
def printer_info() -> dict:
    return {
        "result": {
            "state": state.webhooks_state,
            "state_message": (
                "Printer is ready"
                if state.webhooks_state == "ready"
                else f"Klippy state: {state.webhooks_state}"
            ),
            "hostname": _SIMULATED_HOSTNAME,
            "software_version": _SIMULATED_KLIPPER_VERSION,
            "cpu_info": "Simulated CPU - QIDI Moonraker Simulator",
            "klipper_path": "/home/pi/klipper",
            "python_path": "/home/pi/klippy-env/bin/python",
            "log_file": "/home/pi/printer_data/logs/klippy.log",
            "config_file": _SIMULATED_KLIPPER_CONFIG_FILE,
        }
    }

@app.get("/printer/objects/query")
def printer_objects_query() -> dict:
    return {
        "result": {
            "status": {
                "print_stats": {
                    "state": state.print_stats_state,
                    "filename": state.print_stats_filename,
                    "print_duration": state.print_stats_print_duration,
                },
                "virtual_sdcard": {
                    "progress": state.virtual_sdcard_progress,
                },
                "webhooks": {
                    "state": state.webhooks_state,
                },
            }
        }
    }

@app.post("/server/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    print_flag: str | None = Form(None, alias="print"),
) -> dict:
    content = await file.read()
    if print_flag == "true":
        state.print_stats_state = "printing"
        state.print_stats_filename = file.filename
        state.print_stats_print_duration = 0.0
        state.virtual_sdcard_progress = 0.0
    return {
        "result": {
            "item": {
                "path": f"gcodes/{file.filename}",
                "root": "gcodes",
                "size": len(content),
            },
            "print_started": print_flag == "true",
        }
    }

@app.post("/printer/gcode/script")
def gcode_script(script: str = "") -> dict:
    return {"result": "ok"}

@app.post("/printer/print/cancel")
def cancel_print() -> dict:
    state.print_stats_state = "cancelled"
    return {"result": "ok"}

@app.post("/printer/print/pause")
def pause_print() -> dict:
    state.print_stats_state = "paused"
    return {"result": "ok"}

@app.post("/printer/print/resume")
def resume_print() -> dict:
    state.print_stats_state = "printing"
    return {"result": "ok"}

_WS_BROADCAST_POLL_INTERVAL_SECONDS = 0.05

def _websocket_status_snapshot(objects: List[str]) -> Dict[str, Dict[str, Any]]:
    full: Dict[str, Dict[str, Any]] = {
        "print_stats": {
            "state": state.print_stats_state,
            "filename": state.print_stats_filename,
            "print_duration": state.print_stats_print_duration,
        },
        "virtual_sdcard": {"progress": state.virtual_sdcard_progress},
        "webhooks": {"state": state.webhooks_state},
        "extruder": {
            "temperature": state.extruder_temperature,
            "target": state.extruder_target,
        },
        "heater_bed": {
            "temperature": state.heater_bed_temperature,
            "target": state.heater_bed_target,
        },
    }
    return {name: full[name] for name in objects if name in full}

@app.websocket("/websocket")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    subscribed_objects: Optional[List[str]] = None
    last_snapshot: Dict[str, Dict[str, Any]] = {}
    broadcaster_task: Optional["asyncio.Task[None]"] = None

    async def _broadcast_loop() -> None:
        nonlocal last_snapshot
        while True:
            await asyncio.sleep(_WS_BROADCAST_POLL_INTERVAL_SECONDS)
            if not subscribed_objects:
                continue
            current = _websocket_status_snapshot(subscribed_objects)
            delta = {
                name: value
                for name, value in current.items()
                if value != last_snapshot.get(name)
            }
            if delta:
                last_snapshot = current
                await websocket.send_json(
                    {
                        "jsonrpc": "2.0",
                        "method": "notify_status_update",
                        "params": [delta, time.time()],
                    }
                )

    try:
        while True:
            message = await websocket.receive_json()
            method = message.get("method")
            req_id = message.get("id")
            if method == "printer.objects.subscribe":
                params = message.get("params") or {}
                requested_objects = list((params.get("objects") or {}).keys())
                subscribed_objects = requested_objects
                last_snapshot = _websocket_status_snapshot(requested_objects)
                if broadcaster_task is None:
                    broadcaster_task = asyncio.create_task(_broadcast_loop())
                await websocket.send_json(
                    {
                        "id": req_id,
                        "result": {
                            "eventtime": time.time(),
                            "status": last_snapshot,
                        },
                    }
                )
            elif req_id is not None:

                await websocket.send_json({"id": req_id, "result": {}})
    except WebSocketDisconnect:
        pass
    finally:
        if broadcaster_task is not None:
            broadcaster_task.cancel()
            try:
                await broadcaster_task
            except asyncio.CancelledError:
                pass
