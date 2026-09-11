"""
Fixture dùng chung cho `tests/realtime/` (E2-1/C5).

Quyết định cục bộ chốt tại chunk này (không phải `D-00X`) — tái dùng vs
viết độc lập, cùng tinh thần đã ghi ở `tests/heartbeat/conftest.py`:

- `simulator_factory`: **duplicate có chủ đích** từ
  `tests/moonraker_simulator/test_integration.py`/`tests/heartbeat/
  conftest.py` (mỗi package test tự chứa fixture khởi động simulator
  của mình, đúng tiền lệ đã lặp lại 2 lần trước đó) — nhưng MỞ RỘNG
  thêm khả năng `restart()` mà 2 bản trước không cần: package này là
  package DUY NHẤT cần mô phỏng "server tắt rồi bật lại" để test
  reconnect (AC gốc E2-1 "tự reconnect khi rớt mạng") — bind lại ĐÚNG
  cùng port đã cấp trước đó (`uvicorn`/`asyncio` mặc định
  `reuse_address=True` trên POSIX, xác nhận qua kiểm chứng thủ công tại
  chunk này — bind lại ngay sau khi thread trước `join()` xong không
  gặp lỗi "Address already in use") để `WebsocketPool` (đã đọc `host`/
  `port` cố định từ DB lúc `start()`, Quyết định 7 — không hot-reload)
  tự reconnect đúng vào cùng địa chỉ, không cần sửa DB giữa chừng.
- KHÔNG dùng `client`/`app.main.app` ở package này (khác
  `tests/heartbeat/conftest.py`) — `tests/realtime/` test thẳng
  `PrinterWebsocketConnection`/`WebsocketPool` (`app/realtime/`) độc
  lập với FastAPI app chính, không cần dựng `lifespan` thật (wiring
  `lifespan` đã có ở C3, không phải phạm vi test lại ở đây).
- `realtime_db_path`/`insert_printer`: cùng kỹ thuật "DB tạm đã chạy
  migration + chèn thẳng qua sqlite3" như `tests/heartbeat/conftest.py`
  (`heartbeat_db_path`/`insert_printer`), tên riêng để không đụng
  fixture cùng tên ở package khác (mỗi package test độc lập, không
  import chéo trừ trường hợp có lý do kỹ thuật thật như `client` ở
  heartbeat).
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional

import pytest
import uvicorn

import tools.moonraker_simulator.app as simulator_app
from app.db.migrate import run_migrations
from tools.moonraker_simulator.state import SimulatorState

SIMULATOR_HOST = "127.0.0.1"
_STARTUP_TIMEOUT_SECONDS = 5.0
_STARTUP_POLL_INTERVAL_SECONDS = 0.05

class _ServerThread(threading.Thread):
    """Chạy `uvicorn.Server.serve()` (coroutine) trong 1 thread nền riêng
    (duplicate từ `tests/moonraker_simulator/test_integration.py`)."""

    def __init__(self, server: uvicorn.Server) -> None:
        super().__init__(daemon=True)
        self._server = server

    def run(self) -> None:
        asyncio.run(self._server.serve())

def _bind(host: str, port: int) -> tuple:
    """Reset `simulator_app.state` (cô lập giữa các lần bind/restart) rồi
    khởi động 1 `uvicorn.Server` mới trên `host`/`port` chỉ định (`port=0`
    -> OS tự cấp cổng ngẫu nhiên). Trả `(server, thread, actual_port)`."""
    simulator_app.state = SimulatorState()

    config = uvicorn.Config(simulator_app.app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = _ServerThread(server)
    thread.start()

    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Simulator không khởi động kịp trong {_STARTUP_TIMEOUT_SECONDS}s"
            )
        time.sleep(_STARTUP_POLL_INTERVAL_SECONDS)

    actual_port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, actual_port

@dataclass
class SimulatorHandle:
    """1 instance simulator đã khởi động qua `simulator_factory`.

    `restart()` — MỚI so với `tests/heartbeat/conftest.py`/
    `tests/moonraker_simulator/test_integration.py` (xem docstring
    module) — dừng thread hiện tại rồi bind lại ĐÚNG cùng `port`, mô
    phỏng "máy in mất kết nối rồi mạng phục hồi" cho test reconnect của
    `WebsocketPool`."""

    host: str
    port: int
    stop: Callable[[], None]
    restart: Callable[[], None]

@pytest.fixture()
def simulator_factory() -> Iterator[Callable[..., SimulatorHandle]]:
    handles: List[SimulatorHandle] = []

    def _start(host: str = SIMULATOR_HOST) -> SimulatorHandle:
        server, thread, port = _bind(host, 0)
        state_box = {"server": server, "thread": thread, "stopped": False}

        def _stop() -> None:
            if state_box["stopped"]:
                return
            state_box["stopped"] = True
            state_box["server"].should_exit = True
            state_box["thread"].join(timeout=_STARTUP_TIMEOUT_SECONDS)

        def _restart() -> None:
            _stop()
            new_server, new_thread, _ = _bind(host, port)
            state_box["server"] = new_server
            state_box["thread"] = new_thread
            state_box["stopped"] = False

        handle = SimulatorHandle(host=host, port=port, stop=_stop, restart=_restart)
        handles.append(handle)
        return handle

    try:
        yield _start
    finally:
        for handle in handles:
            handle.stop()

@pytest.fixture()
def realtime_db_path(tmp_path) -> str:
    """DB file tạm riêng cho `tests/realtime/` — đã chạy `run_migrations`."""
    db_path = str(tmp_path / "test_realtime.db")
    run_migrations(db_path)
    return db_path

_INSERT_PRINTER_SQL = (
    "INSERT INTO printers (name, ip, moonraker_port, api_key) VALUES (?, ?, ?, ?)"
)

@pytest.fixture()
def insert_printer(realtime_db_path: str) -> Callable[..., int]:
    """Chèn thẳng 1 dòng `printers` vào `realtime_db_path` qua `sqlite3`
    thô (KHÔNG qua `register_printer`, cùng lý do đã ghi ở
    `tests/heartbeat/conftest.py`: cần trỏ `moonraker_port` thẳng tới
    simulator giả lập mà không cần validate kết nối lúc insert). Trả về
    `printer_id` vừa tạo."""

    def _insert(
        ip: str,
        moonraker_port: int,
        *,
        name: str = "Realtime Test Printer",
        api_key: Optional[str] = None,
    ) -> int:
        connection = sqlite3.connect(realtime_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL, (name, ip, moonraker_port, api_key)
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert
