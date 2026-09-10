"""
Pool quản lý N kết nối WebSocket đồng thời tới N máy in (E2-1/C3,
`docs/State_E2-1_v4.md` mục "CHUNK KẾ TIẾP CẦN CHẠY").

Mỗi máy (`printers` trong DB) được gán 1 `asyncio.Task` riêng
(`_run_printer_connection`), mỗi task tự quản vòng đời 1
`PrinterWebsocketConnection` (C2, `app/realtime/connection.py`):
connect -> subscribe -> nhận notification -> khi rớt -> backoff ->
reconnect -> lặp lại - tới khi bị huỷ (`WebsocketPool.stop()`, gọi từ
`lifespan` shutdown).

**Quyết định 2 (backoff/reconnect, chốt tại C0)** - `moonraker-api`
KHÔNG tự reconnect (đã xác nhận qua đọc trực tiếp source ở C0). Vòng
lặp CHỦ ĐỘNG ở `_run_printer_connection` phát hiện rớt kết nối qua
`connection.disconnected` (`asyncio.Event`, được `PrinterWebsocketConnection.
state_changed()` set khi rơi từ CONNECTED về STOPPED - C3 sửa
`connection.py` để thêm cơ chế báo hiệu này, KHÔNG polling
`is_connected`), rồi chờ theo backoff cấp số nhân
(`WS_RECONNECT_INITIAL_BACKOFF_SECONDS * WS_RECONNECT_BACKOFF_MULTIPLIER
** consecutive_failures`, chặn trần `WS_RECONNECT_MAX_BACKOFF_SECONDS`,
`app/realtime/state.py`, C1) trước khi gọi lại `connect()`.
`consecutive_failures` reset về 0 ngay khi `connect()` thành công.
`connect()` của thư viện có thể RAISE exception (không chỉ trả `False`)
khi lỗi xảy ra trong lúc bắt tay kết nối (xác nhận qua đọc
`WebsocketClient.connect()`/`_run()`: lỗi trong `_run()` được set làm
exception của chính `Future` mà `connect()` đang `await`) - vòng lặp
bắt cả 2 trường hợp (raise và trả `False`) đều coi là "connect thất
bại", cùng 1 nhánh backoff.

**Đọc danh sách máy 1 lần khi khởi động** - `_fetch_all_printers` SELECT
TOÀN BỘ bảng `printers` trong 1 lần round-trip DB (KHÔNG gọi
`fetch_printer_connection_params`, `connection.py`/C2, lặp lại cho
từng máy - hàm đó vẫn giữ nguyên cho trường hợp cần 1 máy đơn lẻ, dùng
bởi kiểm chứng thủ công/tương lai, KHÔNG xoá).

**Quyết định 7 (snapshot, KHÔNG hot-reload, chốt tại C0)** -
`WebsocketPool.start()` chỉ đọc DB đúng 1 lần lúc gọi (từ `lifespan`
startup) - máy thêm/xoá sau đó qua `POST`/`DELETE /printers` KHÔNG được
pool tự nhận biết ở story này (ghi ở "Rủi ro để lại" của
`docs/State_E2-1_v4.md`).

**Xử lý rủi ro `aiohttp.ClientSession` không tự đóng (phát hiện ở C2)**
- mỗi task tự tạo ĐÚNG 1 `aiohttp.ClientSession` riêng, truyền tường
minh vào `PrinterWebsocketConnection(session=...)` (C3 sửa
`connection.py` để nhận tham số này) thay vì để thư viện tự tạo ngầm,
rồi tự đóng session đó trong khối `finally` của
`_run_printer_connection` - đảm bảo đóng cả khi task chạy bình thường
bị `cancel()` (shutdown) lẫn khi vòng lặp thoát vì lý do khác (không có
lối thoát nào khác thực tế ở chunk này ngoài bị huỷ, nhưng `finally`
vẫn là nơi đúng để đảm bảo dọn dẹp trong mọi trường hợp).

**Wiring `lifespan` (Quyết định 6)** - tiền lệ `app/heartbeat/
scheduler.py` (E1-4/C3): `start_websocket_pool()`/`stop_websocket_pool()`
gọi từ `app/main.py` `lifespan`, đối xứng với
`start_heartbeat_scheduler()`/`stop_heartbeat_scheduler()`. Khác biệt kỹ
thuật: heartbeat là 1 task lặp gọi hàm ĐỒNG BỘ qua `asyncio.to_thread`;
ở đây là N task async-native riêng biệt (mỗi máy 1 task) vì
`moonraker-api`/`aiohttp` đã async-native, không cần `to_thread`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Dict, List, Optional, Tuple

import aiohttp

from app.db.migrate import DEFAULT_DB_PATH
from app.realtime.connection import PrinterWebsocketConnection
from app.realtime.state import (
    WS_RECONNECT_BACKOFF_MULTIPLIER,
    WS_RECONNECT_INITIAL_BACKOFF_SECONDS,
    WS_RECONNECT_MAX_BACKOFF_SECONDS,
    RealtimeStateStore,
)

_LOGGER = logging.getLogger(__name__)

_SELECT_ALL_PRINTERS_SQL = "SELECT id, ip, moonraker_port, api_key FROM printers"

def _fetch_all_printers(
    db_path: str = DEFAULT_DB_PATH,
) -> List[Tuple[int, str, int, Optional[str]]]:
    """Đọc `(id, ip, moonraker_port, api_key)` của TOÀN BỘ máy trong
    bảng `printers`, 1 lần round-trip DB (xem docstring module)."""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(_SELECT_ALL_PRINTERS_SQL).fetchall()
    finally:
        connection.close()
    return rows

def _compute_backoff_seconds(consecutive_failures: int) -> float:
    """Backoff cấp số nhân (Quyết định 2), chặn trần
    `WS_RECONNECT_MAX_BACKOFF_SECONDS` - cùng công thức đã ghi ở
    `app/realtime/state.py`."""
    backoff = WS_RECONNECT_INITIAL_BACKOFF_SECONDS * (
        WS_RECONNECT_BACKOFF_MULTIPLIER**consecutive_failures
    )
    return min(backoff, WS_RECONNECT_MAX_BACKOFF_SECONDS)

async def _run_printer_connection(
    printer_id: int,
    host: str,
    port: int,
    api_key: Optional[str],
    store: RealtimeStateStore,
) -> None:
    """Vòng đời 1 kết nối cho 1 máy - chạy trong 1 `asyncio.Task` riêng
    (1 máy lỗi/rớt không ảnh hưởng máy khác, mỗi task độc lập hoàn
    toàn): connect -> (nếu thành công) chờ tới khi rớt -> backoff ->
    reconnect -> lặp lại. Chạy tới khi bị `asyncio.CancelledError`
    (`WebsocketPool.stop()`) - khối `finally` luôn đóng session tự tạo,
    kể cả khi bị huỷ giữa chừng lúc đang `connect()`/chờ `disconnected`.
    """
    session = aiohttp.ClientSession()
    connection = PrinterWebsocketConnection(
        printer_id=printer_id,
        host=host,
        port=port,
        api_key=api_key,
        store=store,
        session=session,
    )
    consecutive_failures = 0
    try:
        while True:
            try:
                connected = await connection.connect()
            except asyncio.CancelledError:
                raise
            except Exception as error:

                connected = False
                _LOGGER.warning(
                    "Printer %s: lỗi khi connect(): %s", printer_id, error
                )

            if not connected:
                consecutive_failures += 1
                backoff_seconds = _compute_backoff_seconds(consecutive_failures)
                _LOGGER.info(
                    "Printer %s: connect() thất bại (lần %d liên tiếp), "
                    "thử lại sau %.1fs",
                    printer_id,
                    consecutive_failures,
                    backoff_seconds,
                )
                await asyncio.sleep(backoff_seconds)
                continue

            consecutive_failures = 0

            await connection.disconnected.wait()
            _LOGGER.info(
                "Printer %s: mất kết nối WS, sẽ backoff rồi reconnect",
                printer_id,
            )
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await connection.disconnect()
        except Exception:

            _LOGGER.debug(
                "Printer %s: lỗi khi disconnect() lúc dọn dẹp (bỏ qua)",
                printer_id,
                exc_info=True,
            )
        await session.close()

class WebsocketPool:
    """Quản lý N `asyncio.Task` (1 task/máy). Snapshot danh sách máy từ
    DB đúng 1 lần lúc `start()` (Quyết định 7, KHÔNG hot-reload)."""

    def __init__(self, store: RealtimeStateStore, db_path: str = DEFAULT_DB_PATH) -> None:
        self.store = store
        self.db_path = db_path
        self._tasks: Dict[int, "asyncio.Task[None]"] = {}

    def start(self) -> None:
        """Đọc TOÀN BỘ danh sách máy (1 lần SELECT, xem
        `_fetch_all_printers`) rồi tạo N task, mỗi task 1 kết nối. Gọi
        từ FastAPI `lifespan` lúc startup (`app/main.py`).

        Lỗi đọc DB lúc khởi động (ví dụ chưa chạy migration, bảng
        `printers` chưa tồn tại) KHÔNG được làm crash hẳn `lifespan`
        startup - pool WS chỉ là 1 phần phụ trợ (real-time, in-memory,
        Quyết định 4), các route/logic khác (Printer Registry,
        heartbeat) không phụ thuộc vào nó. Quyết định cục bộ MỚI phát
        sinh khi triển khai chunk này (`docs/State_E2-1_v5.md`): log lỗi
        rồi coi như 0 máy (`_tasks` rỗng, không phải lỗi cần chặn
        service khởi động) - cùng tinh thần "1 lỗi không mong đợi không
        được giết hẳn cơ chế nền" đã áp dụng cho
        `app/heartbeat/scheduler.py::_heartbeat_loop` (E1-4/C3), chỉ
        khác là ở đây lỗi xảy ra ngay tại bước đọc DB 1 lần lúc khởi
        động (đồng bộ) thay vì trong 1 vòng lặp bất đồng bộ.
        """
        try:
            printers = _fetch_all_printers(self.db_path)
        except sqlite3.Error as error:
            _LOGGER.warning(
                "WebsocketPool: không đọc được danh sách máy từ DB (%s) - "
                "khởi động với 0 kết nối, sẽ cần restart service để pool "
                "nhận máy sau khi DB sẵn sàng (Quyết định 7, không "
                "hot-reload)",
                error,
            )
            printers = []
        for printer_id, host, port, api_key in printers:
            self._tasks[printer_id] = asyncio.create_task(
                _run_printer_connection(printer_id, host, port, api_key, self.store)
            )
        _LOGGER.info("WebsocketPool: khởi động %d kết nối", len(self._tasks))

    async def stop(self) -> None:
        """Huỷ sạch mọi task (`task.cancel()` + `await` để đảm bảo
        `finally` trong `_run_printer_connection` chạy xong, đóng mọi
        `ClientSession`) - không rò rỉ (AC). Gọi từ FastAPI `lifespan`
        lúc shutdown."""
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

def start_websocket_pool(
    store: RealtimeStateStore, db_path: str = DEFAULT_DB_PATH
) -> WebsocketPool:
    """Tạo + khởi động `WebsocketPool`. Gọi từ FastAPI `lifespan` lúc
    startup, đối xứng `start_heartbeat_scheduler()` (E1-4/C3)."""
    pool = WebsocketPool(store=store, db_path=db_path)
    pool.start()
    return pool

async def stop_websocket_pool(pool: WebsocketPool) -> None:
    """Huỷ pool. Gọi từ FastAPI `lifespan` lúc shutdown, đối xứng
    `stop_heartbeat_scheduler()` (E1-4/C3)."""
    await pool.stop()
