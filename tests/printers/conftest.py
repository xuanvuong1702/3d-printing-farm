"""
Fixture dùng chung cho `tests/printers/` (E1-1/C2).

Đặt fixture riêng ở đây thay vì import lại từ `tests.drivers.conftest`
— file đó thuộc story E0-6 đã khoá, không phải nơi để tầng khác import
fixture dùng chung; tạo `conftest.py` mới trong package test của chính
story này (E1-1) là cách chuẩn của pytest, đúng tiền lệ E0-6/C3 (khi
đó cũng không import lại từ `tests/moonraker_simulator/`).

2 fixture:
- `simulator`: y hệt pattern của `tests/drivers/conftest.py` — khởi
  động 1 instance Moonraker simulator thật trên cổng ngẫu nhiên trong
  1 thread nền cho mỗi test, trả về port đã bind.
- `client`: `TestClient` bọc `app.main.app`, đã monkeypatch
  `app.printers.router.register_printer` để dùng DB file tạm
  (`run_migrations(tmp_path)`) thay vì `DEFAULT_DB_PATH` — không sửa
  `app/printers/router.py`/`service.py` (default `db_path` của
  `register_printer` được giữ nguyên cho production, chỉ override ở
  tầng test qua `monkeypatch.setattr` trên module `router`, đúng cách
  C1 đã thiết kế sẵn tham số `db_path` cho mục đích test injection —
  xem `docs/State_E1-1_v3.md` mục "Chunk plan" C2).
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Iterator

import pytest
import uvicorn
from fastapi.testclient import TestClient

import app.main as main_module
import app.printers.router as printers_router_module
import tools.moonraker_simulator.app as simulator_app
from app.db.migrate import run_migrations
from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT
from app.printers.service import register_printer as _real_register_printer
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

@pytest.fixture()
def simulator_on_default_port() -> Iterator[int]:
    """
    Giống fixture `simulator`, nhưng bind đúng `DEFAULT_MOONRAKER_PORT`
    (7125, D-001) thay vì cổng ngẫu nhiên — dùng riêng cho case AC
    "moonraker_port dùng giá trị mặc định 7125 khi không truyền", nơi
    cần test end-to-end thật (đăng ký không truyền `moonraker_port`,
    xác nhận request tới đúng cổng 7125 và response lưu đúng giá trị
    đó), không chỉ test giá trị default ở tầng Pydantic.
    """
    simulator_app.state = SimulatorState()

    config = uvicorn.Config(
        simulator_app.app,
        host=SIMULATOR_HOST,
        port=DEFAULT_MOONRAKER_PORT,
        log_level="warning",
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

    try:
        yield DEFAULT_MOONRAKER_PORT
    finally:
        server.should_exit = True
        thread.join(timeout=_STARTUP_TIMEOUT_SECONDS)

@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """
    `TestClient` cho `app.main.app`, dùng DB file tạm riêng cho mỗi
    test (không dùng `:memory:` — xem lý do đã ghi ở `State_E1-1_v2.md`
    mục chunk plan C2: FastAPI `TestClient` có thể chạy request trong
    thread/connection khác connection đã tạo bảng, mà `:memory:` là DB
    riêng theo từng kết nối `sqlite3.connect`).

    Override qua `monkeypatch.setattr` trên `app.printers.router` (nơi
    tên `register_printer` được bind vào lúc `from ... import
    register_printer`) — không sửa `app/printers/router.py` hay
    `service.py`.
    """
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    def _register_printer_with_tmp_db(request):
        return _real_register_printer(request, db_path=db_path)

    monkeypatch.setattr(
        printers_router_module, "register_printer", _register_printer_with_tmp_db
    )

    with TestClient(main_module.app) as test_client:
        yield test_client
