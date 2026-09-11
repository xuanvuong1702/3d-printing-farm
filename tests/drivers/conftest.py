"""
Fixture dùng chung cho `tests/drivers/` — khởi động Moonraker simulator
thật trong 1 thread nền, để test `BaseKlipperDriver` gọi thật qua HTTP
(không mock), giống đúng cách `tests/moonraker_simulator/
test_integration.py` (chunk C5 của E0-5) đã làm.

Đặt fixture riêng ở đây thay vì import lại từ
`tests.moonraker_simulator.test_integration` — file đó là test module
đã khoá (thuộc story E0-5 đã `done`), không phải nơi để tầng khác import
fixture dùng chung; tạo `conftest.py` mới trong package test của chính
story này (E0-6) là cách chuẩn của pytest, không sửa file đã khoá.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Iterator

import pytest
import uvicorn

import tools.moonraker_simulator.app as simulator_app
from tools.moonraker_simulator.state import SimulatorState

SIMULATOR_HOST = "127.0.0.1"
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
    mỗi test, trả về port đã bind. Reset `simulator_app.state` về mặc
    định trước khi khởi động để cô lập state giữa các test.
    """
    simulator_app.state = SimulatorState()

    config = uvicorn.Config(
        simulator_app.app, host=SIMULATOR_HOST, port=0, log_level="warning"
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
