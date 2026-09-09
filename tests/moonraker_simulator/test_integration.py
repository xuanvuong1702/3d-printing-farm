"""
Test tích hợp cho Moonraker simulator (`tools/moonraker_simulator`), chunk
C5 của E0-5.

Khác với `tests/moonraker/test_http_client.py` (E0-3, mock `respx` ở tầng
transport HTTP): file này dùng **hàm thật** của `app/moonraker/
http_client.py` gọi qua HTTP thật vào 1 instance simulator thật, xác nhận
toàn bộ luồng API đúng AC gốc của E0-5 ("Dev/test được toàn bộ luồng API
mà không phụ thuộc máy in vật lý").

Quyết định kỹ thuật chốt tại chunk này (xem `docs/Story_E0-5.md` mục
"Chunk C5" để biết lý do đầy đủ):

- **Cách chạy simulator trong test**: `uvicorn.Server` chạy trong 1 thread
  nền của CHÍNH tiến trình pytest (không phải subprocess riêng) — cho phép
  fixture truy cập trực tiếp instance `state` của `tools.moonraker_simulator
  .app` để mutate trực tiếp (cần thiết cho test case "progress đang tăng",
  vì C4 cố tình KHÔNG mô phỏng progress tự tăng theo thời gian thực).
- **Cô lập state giữa các test**: `state` là biến module-level dùng chung
  trong `tools/moonraker_simulator/app.py`; fixture `simulator` gán đè một
  `SimulatorState()` mới vào `tools.moonraker_simulator.app.state` trước
  mỗi test (scope `function`), tránh rò rỉ state giữa các test.
- **Cổng**: bind port 0 (cổng ngẫu nhiên thật do OS cấp), đọc lại cổng thật
  đã gán qua socket của uvicorn server — tránh xung đột cổng nếu pytest
  chạy song song, và không đụng cổng 7125 mặc định (D-001) trong lúc test
  chạy.
"""

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
    """Chạy `uvicorn.Server.serve()` (coroutine) trong 1 thread nền riêng."""

    def __init__(self, server: uvicorn.Server) -> None:
        super().__init__(daemon=True)
        self._server = server

    def run(self) -> None:
        asyncio.run(self._server.serve())

@pytest.fixture()
def simulator() -> Iterator[int]:
    """
    Khởi động 1 instance Moonraker simulator mới trên cổng ngẫu nhiên cho
    mỗi test, trả về port đã bind. Reset `simulator_app.state` về mặc định
    trước khi khởi động để cô lập state giữa các test (xem docstring
    module).
    """
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
    """Instance `state` HIỆN HÀNH của simulator (đọc lại mỗi lần gọi, vì
    fixture gán đè biến module-level trước mỗi test)."""
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
    """
    Tái tạo đúng bug thật đã ghi trong checklist Moonraker (Decisions.md):
    nếu gửi `print` qua QUERY STRING thay vì multipart form field, server
    KHÔNG bắt đầu in nhưng vẫn trả 200 - không phải lỗi client thấy được.

    Gọi `httpx.post` thủ công (mô phỏng đúng cách gọi SAI mà
    `http_client.py::upload_and_print` KHÔNG mắc phải - hàm thật luôn gửi
    `print` qua `data=`, xem test khác ở trên) để xác nhận simulator tái
    tạo đúng hành vi bug này, không phải là simulator tự dễ dãi bỏ qua mọi
    trường hợp.
    """
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

def test_simulator_state_is_reset_between_tests(simulator: int) -> None:
    status = hc.get_status(HOST, port=simulator)
    assert status.canonical_status == hc.CANONICAL_IDLE
    assert status.filename is None
