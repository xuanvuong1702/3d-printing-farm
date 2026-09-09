
from __future__ import annotations

from fastapi import FastAPI

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
