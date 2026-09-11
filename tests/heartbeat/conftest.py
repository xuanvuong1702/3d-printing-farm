"""
Fixture dùng chung cho `tests/heartbeat/` (E1-4/C4).

Quyết định cục bộ chốt tại chunk này (không phải `D-00X`) — tái dùng vs
viết độc lập (state yêu cầu quyết định rõ, xem `docs/State_E1-4_v5.md`
mục "CHUNK KẾ TIẾP CẦN CHẠY"):

- `simulator_factory`: **duplicate** từ `tests/printers/conftest.py`,
  đúng tiền lệ E0-6/E1-1→E1-3 (mỗi package test tự chứa fixture của
  mình, không import chéo package test khác đã khoá) — fixture này
  không gắn gì với wiring cụ thể của `app.main.app`, an toàn để copy
  y hệt.
- `client`: **import lại trực tiếp** từ `tests/printers/conftest.py`
  (`from tests.printers.conftest import client`) — NGOẠI LỆ so với tiền
  lệ duplicate, có lý do kỹ thuật thật sự (state mục "phạm vi" cho phép
  ngoại lệ khi có lý do rõ ràng): fixture `client` đó không chỉ là hạ
  tầng chung (simulator) mà còn chứa chính xác 2 `monkeypatch` gắn với
  wiring `lifespan` của `app.main.app` (`register_printer` +
  `run_heartbeat_cycle`, cùng trỏ về 1 `db_path` tạm) — đây là hành vi
  runtime thật của service, không phải fixture tiện ích thuần tuý.
  Duplicate y nguyên logic này sẽ tạo 2 bản sao có thể lệch nhau nếu
  `app/main.py`/`app/heartbeat/scheduler.py` đổi cách wiring sau này
  (ví dụ thêm 1 override mới) — 1 bản ở `tests/printers/conftest.py`
  được cập nhật, bản kia ở đây bị quên, gây test giả (false negative:
  service thật đã hỏng nhưng test package `heartbeat` vẫn pass vì đang
  test hành vi wiring cũ). Vì `client` fixture bản chất đã là "hạ tầng
  wiring của toàn service" (không phải hạ tầng riêng của domain
  `printers`), import lại 1 nguồn duy nhất an toàn hơn duplicate ở đây.
  `tests/printers/conftest.py` không đổi (đã khoá) — chỉ import, không
  sửa.

Fixture MỚI ở chunk này (không có ở `tests/printers/conftest.py`):
- `heartbeat_db_path`: DB file tạm (đã chạy `run_migrations`) dùng
  riêng cho `tests/heartbeat/test_service.py` — các test đó gọi thẳng
  `run_heartbeat_cycle(db_path=...)` (hàm THUẦN, không cần
  `app.main.app`/simulator client nào), nên KHÔNG dùng `client` fixture
  (tránh xung đột: `client` khởi động scheduler nền thật qua
  `lifespan`, có thể ghi đè `next_heartbeat_at` cùng lúc test đang tự
  gọi `run_heartbeat_cycle` trực tiếp — 2 nguồn ghi cùng 1 DB, kết quả
  test không xác định).
- `insert_printer`: chèn thẳng 1 dòng `printers` vào `heartbeat_db_path`
  qua `sqlite3` thô (KHÔNG qua `POST /printers`/`register_printer` —
  không cần validate kết nối Moonraker lúc insert, và cần điều khiển
  trực tiếp `consecutive_heartbeat_failures`/`next_heartbeat_at` để dựng
  các kịch bản backoff/"chưa tới lượt" mà `register_printer` không cho
  set tường minh). Cùng kỹ thuật "chèn thẳng qua sqlite3" đã dùng ở
  script kiểm chứng tạm của chunk C2 (xem `docs/Story_E1-4.md` mục
  "Cách chạy/kiểm chứng" chunk C2).
- `fetch_printer`: đọc lại 3 cột liên quan
  (`status`/`consecutive_heartbeat_failures`/`next_heartbeat_at`) của 1
  `printer_id` từ `heartbeat_db_path`, dùng để assert sau khi gọi
  `run_heartbeat_cycle`.

Cập nhật tại chunk E3-4/C2 (xem `docs/State_E3-4_v1.md` mục "CHUNK KẾ
TIẾP CẦN CHẠY"): `insert_printer` nhận thêm tham số tuỳ chọn `is_held`
(mặc định `0`, khớp DEFAULT của cột ở `app/db/schema.py`) - thêm tham
số có default không phá vỡ lời gọi cũ (E1-4). KHÔNG đổi số cột
`fetch_printer` trả về (test E1-4 đã khoá dùng unpack đúng 3 giá trị,
đổi arity sẽ làm vỡ unpack đó) - thêm fixture MỚI riêng
`fetch_is_held` đọc đúng 1 cột `is_held`, dùng cho các test mới của
chunk này (`tests/heartbeat/test_service.py`).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Tuple

import pytest
import uvicorn

import tools.moonraker_simulator.app as simulator_app
from app.db.migrate import run_migrations
from tools.moonraker_simulator.state import SimulatorState

from tests.printers.conftest import client

__all__ = ["client"]

SIMULATOR_HOST = "127.0.0.1"
_STARTUP_TIMEOUT_SECONDS = 5.0
_STARTUP_POLL_INTERVAL_SECONDS = 0.05

class _ServerThread(threading.Thread):
    """Chạy `uvicorn.Server.serve()` (coroutine) trong 1 thread nền riêng."""

    def __init__(self, server: uvicorn.Server) -> None:
        super().__init__(daemon=True)
        self._server = server

    def run(self) -> None:
        import asyncio

        asyncio.run(self._server.serve())

@dataclass
class SimulatorHandle:
    """1 instance simulator đã khởi động qua `simulator_factory` — `stop()`
    tắt được ngay trong thân test, không phải đợi tới lúc fixture teardown.
    Y hệt `tests/printers/conftest.py::SimulatorHandle` (duplicate có chủ
    đích, xem docstring module)."""

    host: str
    port: int
    stop: Callable[[], None]

@pytest.fixture()
def simulator_factory() -> Iterator[Callable[..., SimulatorHandle]]:
    """Duplicate từ `tests/printers/conftest.py` (xem docstring module này
    cho lý do duplicate thay vì import)."""
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

@pytest.fixture()
def heartbeat_db_path(tmp_path) -> str:
    """DB file tạm riêng cho `test_service.py` — đã chạy `run_migrations`,
    KHÔNG dùng chung `tmp_path` với `client` (tránh 2 nguồn ghi đồng thời,
    xem docstring module)."""
    db_path = str(tmp_path / "test_heartbeat_service.db")
    run_migrations(db_path)
    return db_path

_INSERT_PRINTER_SQL = """
INSERT INTO printers (
    name, ip, moonraker_port, model, api_key, klipper_version,
    consecutive_heartbeat_failures, next_heartbeat_at, is_held
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

@pytest.fixture()
def insert_printer(heartbeat_db_path: str) -> Callable[..., int]:
    """Chèn thẳng 1 dòng `printers` vào `heartbeat_db_path` (KHÔNG qua
    `register_printer` — xem docstring module cho lý do). Trả về
    `printer_id` vừa tạo.

    Tham số `is_held` (mới, E3-4/C2) mặc định `0` — khớp DEFAULT của
    cột ở `app/db/schema.py`, cho phép dựng kịch bản "máy đã đang bị
    hold từ trước" (`is_held=1`) cho test ngoại lệ tự gỡ (điểm 5,
    `docs/State_E3-4_v1.md`)."""

    def _insert(
        ip: str,
        moonraker_port: int,
        *,
        name: str = "Heartbeat Test Printer",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        klipper_version: Optional[str] = None,
        consecutive_heartbeat_failures: int = 0,
        next_heartbeat_at: Optional[str] = None,
        is_held: int = 0,
    ) -> int:
        connection = sqlite3.connect(heartbeat_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL,
                (
                    name,
                    ip,
                    moonraker_port,
                    model,
                    api_key,
                    klipper_version,
                    consecutive_heartbeat_failures,
                    next_heartbeat_at,
                    is_held,
                ),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

@pytest.fixture()
def fetch_printer(heartbeat_db_path: str) -> Callable[[int], Tuple]:
    """Đọc lại `(status, consecutive_heartbeat_failures, next_heartbeat_at)`
    của 1 `printer_id` từ `heartbeat_db_path`, dùng để assert sau khi gọi
    `run_heartbeat_cycle`."""

    def _fetch(printer_id: int) -> Tuple:
        connection = sqlite3.connect(heartbeat_db_path)
        try:
            row = connection.execute(
                "SELECT status, consecutive_heartbeat_failures, "
                "next_heartbeat_at FROM printers WHERE id = ?",
                (printer_id,),
            ).fetchone()
        finally:
            connection.close()
        return row

    return _fetch

@pytest.fixture()
def fetch_is_held(heartbeat_db_path: str) -> Callable[[int], bool]:
    """Đọc lại cột `is_held` (D-010) của 1 `printer_id` từ
    `heartbeat_db_path` — fixture MỚI, E3-4/C2 (xem docstring module
    cho lý do không mở rộng arity của `fetch_printer` đã khoá)."""

    def _fetch(printer_id: int) -> bool:
        connection = sqlite3.connect(heartbeat_db_path)
        try:
            (is_held,) = connection.execute(
                "SELECT is_held FROM printers WHERE id = ?",
                (printer_id,),
            ).fetchone()
        finally:
            connection.close()
        return bool(is_held)

    return _fetch

@pytest.fixture()
def set_next_heartbeat_at(heartbeat_db_path: str) -> Callable[[int, str], None]:
    """Ép `next_heartbeat_at` của 1 `printer_id` về 1 giá trị chỉ định
    (thường là quá khứ, để mô phỏng "đã tới lượt lại" giữa 2 lần gọi
    `run_heartbeat_cycle` liên tiếp trong 1 test — dựng kịch bản backoff
    2+ lần lỗi liên tiếp, xem `test_service.py`)."""

    def _set(printer_id: int, next_heartbeat_at: str) -> None:
        connection = sqlite3.connect(heartbeat_db_path)
        try:
            connection.execute(
                "UPDATE printers SET next_heartbeat_at = ? WHERE id = ?",
                (next_heartbeat_at, printer_id),
            )
            connection.commit()
        finally:
            connection.close()

    return _set
