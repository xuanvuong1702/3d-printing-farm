
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

DEFAULT_MOONRAKER_PORT = 7125

INFO_TIMEOUT_SECONDS = 8.0
UPLOAD_TIMEOUT_SECONDS = 120.0

CANONICAL_IDLE = "IDLE"
CANONICAL_PRINTING = "PRINTING"
CANONICAL_PAUSED = "PAUSED"
CANONICAL_FINISHED = "FINISHED"
CANONICAL_STOPPED = "STOPPED"
CANONICAL_ERROR = "ERROR"
CANONICAL_OFFLINE = "OFFLINE"
CANONICAL_UNKNOWN = "UNKNOWN"

_PRINT_STATS_STATE_MAP = {
    "standby": CANONICAL_IDLE,
    "printing": CANONICAL_PRINTING,
    "paused": CANONICAL_PAUSED,
    "complete": CANONICAL_FINISHED,
    "error": CANONICAL_ERROR,
    "cancelled": CANONICAL_STOPPED,
}

ACTIVE_JOB_STATUSES = {CANONICAL_PRINTING, CANONICAL_PAUSED}

class MoonrakerClientError(Exception):
    pass

@dataclass
class PrinterStatus:

    canonical_status: str
    raw_print_stats_state: Optional[str]
    progress_percent: Optional[int]
    time_remaining_seconds: Optional[int]
    filename: Optional[str]

def _base_url(host: str, port: int = DEFAULT_MOONRAKER_PORT) -> str:
    return f"http://{host}:{port}"

def _request(
    method: str,
    host: str,
    path: str,
    *,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
    timeout: float = INFO_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> httpx.Response:
    headers = kwargs.pop("headers", {}) or {}
    if api_key:

        headers["X-Api-Key"] = api_key
    url = f"{_base_url(host, port)}{path}"
    try:
        response = httpx.request(
            method, url, headers=headers, timeout=timeout, **kwargs
        )
    except httpx.RequestError as exc:
        raise MoonrakerClientError(
            f"Không gọi được Moonraker tại {url}: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise MoonrakerClientError(
            f"Moonraker trả lỗi {response.status_code} cho {url}: {response.text}"
        )
    return response

def get_server_info(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    resp = _request("GET", host, "/server/info", port=port, api_key=api_key)
    return resp.json()["result"]

def get_printer_info(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    resp = _request("GET", host, "/printer/info", port=port, api_key=api_key)
    return resp.json()["result"]

def _map_print_stats_state(raw_state: Optional[str]) -> str:
    if raw_state is None:
        return CANONICAL_UNKNOWN
    return _PRINT_STATS_STATE_MAP.get(raw_state, CANONICAL_UNKNOWN)

def get_status(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> PrinterStatus:
    resp = _request(
        "GET",
        host,
        "/printer/objects/query",
        port=port,
        api_key=api_key,
        params={
            "print_stats": "",
            "virtual_sdcard": "",
            "webhooks": "",
        },
    )
    status = resp.json()["result"]["status"]

    webhooks_state = status.get("webhooks", {}).get("state")
    print_stats = status.get("print_stats", {})
    virtual_sdcard = status.get("virtual_sdcard", {})

    raw_state = print_stats.get("state")

    if webhooks_state != "ready":
        canonical_status = CANONICAL_OFFLINE
    else:
        canonical_status = _map_print_stats_state(raw_state)

    pct = virtual_sdcard.get("progress")
    elapsed = print_stats.get("print_duration")

    progress_percent: Optional[int] = None
    time_remaining_seconds: Optional[int] = None
    if pct is not None:

        if pct > 0.02:
            progress_percent = round(pct * 100)
            if elapsed is not None:
                time_remaining_seconds = round(elapsed * (1 - pct) / pct)
        else:
            progress_percent = round(pct * 100)

    filename = None
    if canonical_status in (CANONICAL_PRINTING, CANONICAL_PAUSED):
        filename = print_stats.get("filename")

    return PrinterStatus(
        canonical_status=canonical_status,
        raw_print_stats_state=raw_state,
        progress_percent=progress_percent,
        time_remaining_seconds=time_remaining_seconds,
        filename=filename,
    )

def gcode_script(
    host: str,
    script: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    resp = _request(
        "POST",
        host,
        "/printer/gcode/script",
        port=port,
        api_key=api_key,
        params={"script": script},
    )
    return resp.json()

def upload_and_print(
    host: str,
    filename: str,
    file_content: bytes,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    files = {"file": (filename, file_content)}
    data = {"print": "true"}
    resp = _request(
        "POST",
        host,
        "/server/files/upload",
        port=port,
        api_key=api_key,
        timeout=UPLOAD_TIMEOUT_SECONDS,
        files=files,
        data=data,
    )
    return resp.json()

def upload_file(
    host: str,
    filename: str,
    file_content: bytes,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    files = {"file": (filename, file_content)}
    data = {"print": "false"}
    resp = _request(
        "POST",
        host,
        "/server/files/upload",
        port=port,
        api_key=api_key,
        timeout=UPLOAD_TIMEOUT_SECONDS,
        files=files,
        data=data,
    )
    return resp.json()

def get_file_metadata(
    host: str,
    filename: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    resp = _request(
        "GET",
        host,
        "/server/files/metadata",
        port=port,
        api_key=api_key,
        params={"filename": filename},
    )
    return resp.json()

def cancel_job(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    resp = _request("POST", host, "/printer/print/cancel", port=port, api_key=api_key)
    return resp.json()

def pause_job(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    resp = _request("POST", host, "/printer/print/pause", port=port, api_key=api_key)
    return resp.json()

def resume_job(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    resp = _request("POST", host, "/printer/print/resume", port=port, api_key=api_key)
    return resp.json()

def emergency_stop(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    resp = _request(
        "POST", host, "/printer/emergency_stop", port=port, api_key=api_key
    )
    return resp.json()

def set_device_power(
    host: str,
    device: str,
    action: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    resp = _request(
        "POST",
        host,
        "/machine/device_power/device",
        port=port,
        api_key=api_key,
        json={"device": device, "action": action},
    )
    return resp.json()

def check_if_printing(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> bool:
    status = get_status(host, port=port, api_key=api_key)
    return status.canonical_status in ACTIVE_JOB_STATUSES

def get_job_queue_status(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    resp = _request(
        "GET", host, "/server/job_queue/status", port=port, api_key=api_key
    )
    return resp.json()

def enqueue_job(
    host: str,
    filenames: list[str],
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
    reset: bool = False,
) -> dict:
    resp = _request(
        "POST",
        host,
        "/server/job_queue/job",
        port=port,
        api_key=api_key,
        json={"filenames": filenames, "reset": reset},
    )
    return resp.json()

def start_uploaded_print(
    host: str,
    filename: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> str:
    resp = _request(
        "POST",
        host,
        "/printer/print/start",
        port=port,
        api_key=api_key,
        params={"filename": filename},
    )
    return resp.json()

def get_history_list(
    host: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
    *,
    limit: int = 50,
    since: Optional[float] = None,
) -> dict:
    params: dict[str, Any] = {"limit": limit}
    if since is not None:
        params["since"] = since
    resp = _request(
        "GET",
        host,
        "/server/history/list",
        port=port,
        api_key=api_key,
        params=params,
    )
    return resp.json()

