"""
Test tích hợp cho `app/realtime/pool.py` (E2-1/C5) — chính thức hoá lại
các kịch bản đã kiểm chứng thủ công/tạm thời ở C3/C4 (`docs/Story_E2-1.md`
mục "Cách chạy/kiểm chứng" chunk C3/C4), giờ chạy qua `pytest` + N instance
simulator WS THẬT (mỗi máy 1 instance simulator riêng - đúng cách
`http_client.py` gọi theo host/port riêng của từng máy, xem docstring
`tools/moonraker_simulator/state.py`), KHÔNG mock.

Không có `pytest-asyncio` (xác nhận lại, xem `test_connection.py`) - mọi
kịch bản async bọc trong `asyncio.run(...)`.
"""

from __future__ import annotations

import asyncio
import warnings

from app.realtime.pool import WebsocketPool
from app.realtime.state import RealtimeStateStore
from app.moonraker.http_client import CANONICAL_IDLE

import tools.moonraker_simulator.app as simulator_app

_NOTIFICATION_SETTLE_SECONDS = 0.3

_RECONNECT_MAX_WAIT_SECONDS = 8.0
_RECONNECT_POLL_INTERVAL_SECONDS = 0.2

def test_pool_connects_to_multiple_printers_independently(
    simulator_factory, insert_printer, realtime_db_path
) -> None:
    """N máy độc lập (Quyết định 6/C3) - mỗi máy 1 instance simulator
    riêng, 1 `asyncio.Task` riêng trong pool - cả 2 đều có state đúng sau
    khi `start()`."""
    handle_a = simulator_factory(host="127.0.0.1")
    handle_b = simulator_factory(host="127.0.0.2")
    printer_a = insert_printer(handle_a.host, handle_a.port, name="Máy A")
    printer_b = insert_printer(handle_b.host, handle_b.port, name="Máy B")

    async def _scenario(db_path: str):
        store = RealtimeStateStore()
        pool = WebsocketPool(store=store, db_path=db_path)
        pool.start()
        try:
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)
            state_a = await store.get(printer_a)
            state_b = await store.get(printer_b)
            assert state_a is not None
            assert state_b is not None
            assert state_a.canonical_status == CANONICAL_IDLE
            assert state_b.canonical_status == CANONICAL_IDLE
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                await pool.stop()

    asyncio.run(_scenario(realtime_db_path))

def test_pool_one_printer_failure_does_not_affect_others(
    simulator_factory, insert_printer, realtime_db_path
) -> None:
    """1 máy lỗi (cổng không tồn tại - `ClientConnectionError` ngay từ
    lần `connect()` đầu, chưa từng kết nối được lần nào) KHÔNG ảnh hưởng
    máy khác đang khoẻ mạnh - mỗi task độc lập hoàn toàn (Quyết định 6,
    C4 điểm 1)."""
    handle_ok = simulator_factory(host="127.0.0.1")
    printer_ok = insert_printer(handle_ok.host, handle_ok.port, name="Máy khoẻ")

    handle_broken = simulator_factory(host="127.0.0.2")
    broken_port = handle_broken.port
    handle_broken.stop()
    printer_broken = insert_printer(handle_broken.host, broken_port, name="Máy lỗi")

    async def _scenario(db_path: str):
        store = RealtimeStateStore()
        pool = WebsocketPool(store=store, db_path=db_path)
        pool.start()
        try:
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)
            state_ok = await store.get(printer_ok)
            state_broken = await store.get(printer_broken)
            assert state_ok is not None
            assert state_ok.canonical_status == CANONICAL_IDLE
            assert state_broken is None
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                await pool.stop()

    asyncio.run(_scenario(realtime_db_path))

def test_pool_reconnects_after_simulator_restart(
    simulator_factory, insert_printer, realtime_db_path
) -> None:
    """AC gốc E2-1: "tự reconnect khi rớt mạng". Mô phỏng rớt mạng bằng
    `handle.restart()` (dừng simulator rồi bind lại ĐÚNG cùng port,
    `tests/realtime/conftest.py`) - pool phải TỰ phát hiện rớt kết nối
    (`connection.disconnected`, Quyết định 2) rồi backoff + reconnect,
    KHÔNG cần can thiệp gì thêm từ phía pool/DB."""
    handle = simulator_factory()
    printer_id = insert_printer(handle.host, handle.port)

    async def _scenario(db_path: str):
        store = RealtimeStateStore()
        pool = WebsocketPool(store=store, db_path=db_path)
        pool.start()
        try:
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)
            assert await store.get(printer_id) is not None

            handle.restart()

            simulator_app.state.extruder_temperature = 123.0
            reconnected = False
            elapsed = 0.0
            while elapsed < _RECONNECT_MAX_WAIT_SECONDS:
                await asyncio.sleep(_RECONNECT_POLL_INTERVAL_SECONDS)
                elapsed += _RECONNECT_POLL_INTERVAL_SECONDS

                simulator_app.state.extruder_temperature = 123.0
                current = await store.get(printer_id)
                if current is not None and current.extruder_temp == 123.0:
                    reconnected = True
                    break
            assert reconnected is True
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                await pool.stop()

    asyncio.run(_scenario(realtime_db_path))
