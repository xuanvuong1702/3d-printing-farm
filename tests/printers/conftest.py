"""
Fixture dùng chung cho `tests/printers/` (E1-1/C2, E1-2/C2).

Đặt fixture riêng ở đây thay vì import lại từ `tests.drivers.conftest`
— file đó thuộc story E0-6 đã khoá, không phải nơi để tầng khác import
fixture dùng chung; tạo `conftest.py` mới trong package test của chính
story này (E1-1) là cách chuẩn của pytest, đúng tiền lệ E0-6/C3 (khi
đó cũng không import lại từ `tests/moonraker_simulator/`).

Fixture (3 fixture gốc từ E1-1/C2, giữ nguyên không sửa; 1 fixture mới
thêm ở E1-2/C2):
- `simulator`: y hệt pattern của `tests/drivers/conftest.py` — khởi
  động 1 instance Moonraker simulator thật trên cổng ngẫu nhiên trong
  1 thread nền cho mỗi test, trả về port đã bind.
- `simulator_on_default_port`: giống `simulator` nhưng bind đúng
  `DEFAULT_MOONRAKER_PORT` (7125).
- `client`: `TestClient` bọc `app.main.app`, đã monkeypatch
  `app.printers.router.register_printer` để dùng DB file tạm
  (`run_migrations(tmp_path)`) thay vì `DEFAULT_DB_PATH` — không sửa
  `app/printers/router.py`/`service.py` (default `db_path` của
  `register_printer` được giữ nguyên cho production, chỉ override ở
  tầng test qua `monkeypatch.setattr` trên module `router`, đúng cách
  C1 đã thiết kế sẵn tham số `db_path` cho mục đích test injection —
  xem `docs/State_E1-1_v3.md` mục "Chunk plan" C2).
- `simulator_factory` (E1-2/C2, MỚI): factory fixture trả về 1 hàm
  `start(host=...) -> SimulatorHandle` (`SimulatorHandle` có `.port` và
  `.stop()`), cho phép khởi động NHIỀU simulator trên các host khác
  nhau trong CÙNG 1 test, và tắt (`.stop()`) từng simulator giữa
  chừng — khác với `simulator`/`simulator_on_default_port` (chỉ tắt lúc
  fixture teardown, không thao túng được từ trong thân test). Cần cho
  2 case của `GET /printers` (E1-2): (a) mô phỏng 1 máy chuyển từ
  online sang offline giữa chừng test, (b) 2+ máy online đồng thời
  trên 2+ địa chỉ loopback khác nhau (bắt buộc vì cột `printers.ip` có
  `UNIQUE` constraint, không phải `(ip, port)` — không thể đăng ký 2
  máy cùng 1 IP dù khác port, xem `docs/Story_E1-2.md` mục "Cách chạy/
  kiểm chứng" chunk C1). Mọi simulator do factory này khởi động đều
  chạy chung 1 `simulator_app.app` (module-level) nên chia sẻ chung 1
  `simulator_app.state` — không phải vấn đề cho các test dùng fixture
  này, vì các test đó chỉ cần phân biệt "có simulator đang lắng nghe
  hay không" (online/offline), không cần state in/pause khác nhau
  giữa các máy chạy đồng thời.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List

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

@dataclass
class SimulatorHandle:
    """1 instance simulator đã khởi động qua `simulator_factory` — `stop()`
    tắt được ngay trong thân test, không phải đợi tới lúc fixture teardown."""

    host: str
    port: int
    stop: Callable[[], None]

@pytest.fixture()
def simulator_factory() -> Iterator[Callable[..., SimulatorHandle]]:
    """
    Factory (E1-2/C2) — gọi `start(host="127.0.0.1")` để khởi động 1
    simulator mới, nhận lại `SimulatorHandle` (`.port`, `.stop()`). Có
    thể gọi nhiều lần trong cùng 1 test (với `host` khác nhau) để chạy
    nhiều simulator đồng thời. Mọi handle chưa `.stop()` thủ công trong
    thân test đều được tắt tự động lúc fixture teardown (tránh port/
    thread rò rỉ giữa các test).
    """
    handles: List[SimulatorHandle] = []

    def _start(host: str = SIMULATOR_HOST) -> SimulatorHandle:
        simulator_app.state = SimulatorState()

        config = uvicorn.Config(
            simulator_app.app, host=host, port=0, log_level="warning"
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
        stopped = False

        def _stop() -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            server.should_exit = True
            thread.join(timeout=_STARTUP_TIMEOUT_SECONDS)

        handle = SimulatorHandle(host=host, port=port, stop=_stop)
        handles.append(handle)
        return handle

    try:
        yield _start
    finally:
        for handle in handles:
            handle.stop()
