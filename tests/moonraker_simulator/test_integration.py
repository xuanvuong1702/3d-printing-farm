
from __future__ import annotations

import asyncio
import threading
import time
from typing import Iterator

import httpx
import pytest
import uvicorn

import tools.moonraker_simulator.app as simulator_app
from app.moonraker import http_client as hc
from tools.moonraker_simulator.state import SimulatorState

HOST = "127.0.0.1"
_STARTUP_TIMEOUT_SECONDS = 5.0
_STARTUP_POLL_INTERVAL_SECONDS = 0.05

class _ServerThread(threading.Thread):

    def __init__(self, server: uvicorn.Server) -> None:
        super().__init__(daemon=True)
        self._server = server

    def run(self) -> None:
        asyncio.run(self._server.serve())

@pytest.fixture()
def simulator() -> Iterator[int]:
    simulator_app.state = SimulatorState()

    config = uvicorn.Config(
        simulator_app.app, host=HOST, port=0, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = _ServerThread(server)
    thread.start()

    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Simulator không khởi động kịp trong "
                f"{_STARTUP_TIMEOUT_SECONDS}s"
            )
        time.sleep(_STARTUP_POLL_INTERVAL_SECONDS)

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=_STARTUP_TIMEOUT_SECONDS)

def _state() -> SimulatorState:
    return simulator_app.state

def test_get_server_info_shape(simulator: int) -> None:
    result = hc.get_server_info(HOST, port=simulator)
    assert result["klippy_connected"] is True
    assert result["klippy_state"] == "ready"
    assert isinstance(result["moonraker_version"], str)

def test_get_printer_info_shape(simulator: int) -> None:
    result = hc.get_printer_info(HOST, port=simulator)
    assert result["state"] == "ready"
    assert isinstance(result["software_version"], str)

def test_full_lifecycle_flow(simulator: int) -> None:

    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_IDLE

    upload_result = hc.upload_and_print(
        HOST, "benchy.gcode", b"; fake gcode content", port=simulator
    )
    assert upload_result["result"]["print_started"] is True

    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_PRINTING
    assert status.filename == "benchy.gcode"

    gcode_result = hc.gcode_script(HOST, "G28", port=simulator)
    assert gcode_result["result"] == "ok"
    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_PRINTING

    _state().virtual_sdcard_progress = 0.5
    _state().print_stats_print_duration = 300.0

    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_PRINTING

    assert status.progress_percent == 50

    assert status.time_remaining_seconds == 300

    pause_result = hc.pause_job(HOST, port=simulator)
    assert pause_result["result"] == "ok"
    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_PAUSED

    resume_result = hc.resume_job(HOST, port=simulator)
    assert resume_result["result"] == "ok"
    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_PRINTING

    cancel_result = hc.cancel_job(HOST, port=simulator)
    assert cancel_result["result"] == "ok"
    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_STOPPED

    assert hc.check_if_printing(HOST, port=simulator) is False

def test_check_if_printing_true_while_printing(simulator: int) -> None:
    hc.upload_and_print(HOST, "cube.gcode", b"; fake", port=simulator)
    assert hc.check_if_printing(HOST, port=simulator) is True

def test_upload_bug_print_flag_via_query_param_is_silently_ignored(
    simulator: int,
) -> None:
    url = f"http://{HOST}:{simulator}/server/files/upload"
    response = httpx.post(
        url,
        params={"print": "true"},
        files={"file": ("bad.gcode", b"; fake")},
        timeout=hc.UPLOAD_TIMEOUT_SECONDS,
    )
    assert response.status_code == 200
    assert response.json()["result"]["print_started"] is False

    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_IDLE

def test_upload_file_does_not_start_print(simulator: int) -> None:
    result = hc.upload_file(HOST, "plate.gcode", b"; fake gcode content", port=simulator)
    assert result["result"]["print_started"] is False

    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_IDLE

def test_upload_file_then_get_file_metadata_returns_matching_size(
    simulator: int,
) -> None:
    content = b"; fake gcode content, 34 bytes!!!!"
    hc.upload_file(HOST, "plate.gcode", content, port=simulator)

    metadata = hc.get_file_metadata(HOST, "plate.gcode", port=simulator)

    assert metadata["size"] == len(content)
    assert metadata["filename"] == "plate.gcode"
    assert metadata["estimated_time"] > 0

def test_get_file_metadata_unknown_filename_raises_client_error(
    simulator: int,
) -> None:
    with pytest.raises(hc.MoonrakerClientError):
        hc.get_file_metadata(HOST, "chua_tung_upload.gcode", port=simulator)

def test_upload_and_print_also_populates_metadata(simulator: int) -> None:
    content = b"; fake"
    hc.upload_and_print(HOST, "cube.gcode", content, port=simulator)

    metadata = hc.get_file_metadata(HOST, "cube.gcode", port=simulator)
    assert metadata["size"] == len(content)

def test_job_queue_status_empty_by_default(simulator: int) -> None:
    status = hc.get_job_queue_status(HOST, port=simulator)
    assert status == {"queued_jobs": [], "queue_state": "ready"}

def test_enqueue_job_adds_filename_to_queue(simulator: int) -> None:
    hc.upload_file(HOST, "plate.gcode", b"; fake gcode", port=simulator)

    result = hc.enqueue_job(HOST, ["plate.gcode"], port=simulator)

    assert result == {
        "queued_jobs": [{"filename": "plate.gcode"}],
        "queue_state": "ready",
    }

    status = hc.get_job_queue_status(HOST, port=simulator)
    assert status == result

def test_enqueue_job_appends_across_multiple_calls(simulator: int) -> None:
    hc.enqueue_job(HOST, ["a.gcode"], port=simulator)
    hc.enqueue_job(HOST, ["b.gcode"], port=simulator)

    status = hc.get_job_queue_status(HOST, port=simulator)
    assert status["queued_jobs"] == [
        {"filename": "a.gcode"},
        {"filename": "b.gcode"},
    ]

def test_enqueue_job_reset_clears_existing_queue(simulator: int) -> None:
    hc.enqueue_job(HOST, ["a.gcode"], port=simulator)

    hc.enqueue_job(HOST, ["b.gcode"], port=simulator, reset=True)

    status = hc.get_job_queue_status(HOST, port=simulator)
    assert status["queued_jobs"] == [{"filename": "b.gcode"}]

def test_start_uploaded_print_begins_printing_existing_file(
    simulator: int,
) -> None:
    hc.upload_file(HOST, "plate.gcode", b"; fake gcode", port=simulator)

    result = hc.start_uploaded_print(HOST, "plate.gcode", port=simulator)

    assert result == "ok"
    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_PRINTING
    assert status.filename == "plate.gcode"

def test_start_uploaded_print_unknown_filename_raises_client_error(
    simulator: int,
) -> None:
    with pytest.raises(hc.MoonrakerClientError):
        hc.start_uploaded_print(HOST, "chua_tung_upload.gcode", port=simulator)

def test_simulator_state_is_reset_between_tests(simulator: int) -> None:
    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_IDLE
    assert status.filename is None
