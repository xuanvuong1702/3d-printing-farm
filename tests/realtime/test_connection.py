"""
Test tích hợp cho `app/realtime/connection.py` (E2-1/C5) — chính thức hoá
lại các kịch bản đã kiểm chứng thủ công/tạm thời ở C2 (`docs/Story_E2-1.md`
mục "Cách chạy/kiểm chứng" chunk C2), giờ chạy qua `pytest` + simulator WS
THẬT (`tools/moonraker_simulator/app.py`, route `/websocket` mới ở chunk
này) — KHÔNG mock `MoonrakerClient`/`aiohttp`, cùng tinh thần integration
test đã dùng ở `tests/moonraker_simulator/test_integration.py` (E0-5) /
`tests/heartbeat/` (E1-4).

Không có `pytest-asyncio` (`pip show pytest-asyncio` -> not found, xác
nhận lại tại chunk này, cùng phát hiện đã ghi ở
`tests/heartbeat/test_scheduler.py`) -> mọi kịch bản async bọc trong
`asyncio.run(...)` gọi từ 1 hàm test đồng bộ bình thường, KHÔNG viết
`async def test_...`.
"""

from __future__ import annotations

import asyncio

import aiohttp

from app.realtime.connection import PrinterWebsocketConnection
from app.realtime.state import RealtimeStateStore
from app.moonraker.http_client import CANONICAL_IDLE, CANONICAL_PRINTING

import tools.moonraker_simulator.app as simulator_app

_NOTIFICATION_SETTLE_SECONDS = 0.3

async def _connect(handle) -> tuple:
    """Dựng 1 `PrinterWebsocketConnection` + `RealtimeStateStore` thật,
    kết nối vào `handle` (simulator đã khởi động qua `simulator_factory`).
    Trả `(connection, store, session)` - caller chịu trách nhiệm
    `disconnect()`/`session.close()`."""
    store = RealtimeStateStore()
    session = aiohttp.ClientSession()
    connection = PrinterWebsocketConnection(
        printer_id=1,
        host=handle.host,
        port=handle.port,
        api_key=None,
        store=store,
        session=session,
    )
    connected = await connection.connect()
    assert connected is True
    return connection, store, session

def test_connect_subscribe_receives_initial_snapshot(simulator_factory) -> None:
    """`connect()` + `printer.objects.subscribe` (5 object, Quyết định 3)
    -> ghi ngay state ban đầu từ response subscribe (không đợi
    notification đầu tiên) - máy mặc định IDLE, nhiệt độ phòng, chưa có
    target (`tools/moonraker_simulator/state.py`, mặc định mới ở C5)."""
    handle = simulator_factory()

    async def _scenario():
        connection, store, session = await _connect(handle)
        try:
            result = await store.get(1)
            assert result is not None
            assert result.canonical_status == CANONICAL_IDLE
            assert result.progress_percent == 0
            assert result.filename is None
            assert result.extruder_temp == 25.0
            assert result.extruder_target == 0.0
            assert result.bed_temp == 25.0
            assert result.bed_target == 0.0
        finally:
            await connection.disconnect()
            await session.close()

    asyncio.run(_scenario())

def test_notification_updates_store(simulator_factory) -> None:
    """Mutate `state` của simulator (module-level, cùng cách
    `tests/moonraker_simulator/test_integration.py` đã làm cho
    `virtual_sdcard_progress`) -> vòng lặp broadcast của route
    `/websocket` phát `notify_status_update` -> `PrinterWebsocketConnection
    .on_notification()` merge -> `RealtimeStateStore` được cập nhật đúng,
    KHÔNG cần gọi lại HTTP nào (đúng bản chất kênh WS - đẩy tự động)."""
    handle = simulator_factory()

    async def _scenario():
        connection, store, session = await _connect(handle)
        try:
            simulator_app.state.print_stats_state = "printing"
            simulator_app.state.print_stats_filename = "benchy.gcode"
            simulator_app.state.virtual_sdcard_progress = 0.5
            simulator_app.state.print_stats_print_duration = 100.0
            simulator_app.state.extruder_temperature = 200.0
            simulator_app.state.extruder_target = 210.0
            simulator_app.state.heater_bed_temperature = 60.0
            simulator_app.state.heater_bed_target = 60.0

            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)

            result = await store.get(1)
            assert result.canonical_status == CANONICAL_PRINTING
            assert result.progress_percent == 50

            assert result.time_remaining_seconds == 100
            assert result.filename == "benchy.gcode"
            assert result.extruder_temp == 200.0
            assert result.extruder_target == 210.0
            assert result.bed_temp == 60.0
            assert result.bed_target == 60.0
        finally:
            await connection.disconnect()
            await session.close()

    asyncio.run(_scenario())

def test_partial_notification_delta_preserves_other_fields(simulator_factory) -> None:
    """Notification `notify_status_update` chỉ chứa object THAY ĐỔI
    (KHÔNG phải snapshot toàn bộ) - route `/websocket` (C5) chỉ gửi
    object thật sự đổi giá trị mỗi vòng broadcast, đúng hành vi Moonraker
    thật. Xác nhận `_handle_status_delta()` (`connection.py`, C2) merge
    tích luỹ đúng: đổi CHỈ `extruder` ở lần mutate thứ 2 -> các field
    khác (progress/filename đã ghi từ lần mutate đầu) KHÔNG bị mất/reset
    về `None`."""
    handle = simulator_factory()

    async def _scenario():
        connection, store, session = await _connect(handle)
        try:
            simulator_app.state.print_stats_state = "printing"
            simulator_app.state.print_stats_filename = "cube.gcode"
            simulator_app.state.virtual_sdcard_progress = 0.5
            simulator_app.state.print_stats_print_duration = 100.0
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)

            first = await store.get(1)
            assert first.progress_percent == 50
            assert first.filename == "cube.gcode"
            assert first.extruder_temp == 25.0

            simulator_app.state.extruder_temperature = 205.0
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)

            second = await store.get(1)
            assert second.extruder_temp == 205.0

            assert second.progress_percent == 50
            assert second.filename == "cube.gcode"
            assert second.canonical_status == CANONICAL_PRINTING
        finally:
            await connection.disconnect()
            await session.close()

    asyncio.run(_scenario())
