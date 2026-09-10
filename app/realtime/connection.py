"""
Kết nối WebSocket cho 1 máy in (E2-1/C2, cập nhật C3) - wrapper quanh
`MoonrakerClient`/`MoonrakerListener` (thư viện `moonraker-api`, D-002
phần 2).

Phạm vi C2 (`docs/State_E2-1_v3.md`): quản lý ĐÚNG 1 kết nối WS -
`connect()` + gửi `printer.objects.subscribe` (`SUBSCRIBE_OBJECTS`, 5
object, Quyết định 3, `app/realtime/state.py`), nhận notification +
map -> ghi vào `RealtimeStateStore` (đã có từ C1).

Cập nhật C3 (`docs/State_E2-1_v4.md` mục "CHUNK KẾ TIẾP CẦN CHẠY") -
2 thay đổi, cả hai được state C4 dự kiến trước và có lý do rõ:

1. Thêm tham số `session` (truyền thẳng xuống `MoonrakerClient`) - xử
   lý rủi ro phát hiện ở C2 ("Rủi ro/giới hạn để lại" của
   `docs/State_E2-1_v4.md`): nếu không truyền `session=` tường minh,
   thư viện tự tạo 1 `aiohttp.ClientSession` ngầm trong `connect()`
   nhưng `disconnect()` KHÔNG tự đóng session đó -> rò rỉ khi có N kết
   nối dài hạn (pool, C3). `PrinterWebsocketConnection` giờ CHỈ nhận
   session do caller (pool) tạo + sở hữu; việc tạo/đóng session là
   trách nhiệm của `pool.py`, KHÔNG phải của class này (1 class này =
   1 kết nối logic, không nên tự quản vòng đời tài nguyên dùng chung
   tiềm năng).
2. Phát hiện chuyển trạng thái CONNECTED -> STOPPED (để `pool.py` biết
   khi nào cần backoff + gọi lại `connect()`, Quyết định 2) qua
   `asyncio.Event` (`disconnected`) được set trong `state_changed()` -
   KHÔNG polling `is_connected` (đúng yêu cầu của state C3): thư viện
   tự gọi `listener.state_changed(value)` mỗi khi `WebsocketClient`
   đổi `state` (xem `moonraker_api/websockets/websocketclient.py`,
   `state` là property với setter tạo task gọi callback này) - đăng ký
   nhận đúng sự kiện thư viện đã phát ra thay vì tự suy luận qua vòng
   lặp polling. Chỉ set `disconnected` khi từng đạt `CONNECTED` trước
   đó rồi rơi về `STOPPED` (không set ngay từ trạng thái `STOPPED` ban
   đầu lúc chưa từng kết nối - `_connected_once` phân biệt 2 trường hợp
   này), để `pool.py` chỉ coi là "rớt kết nối cần reconnect" đúng 1
   lần/lượt rớt, không nhầm với trạng thái nghỉ ban đầu.

Map trạng thái (D-013) - TÁI DÙNG trực tiếp
`app.moonraker.http_client._map_print_stats_state` (import thẳng,
không viết lại logic map) vì cùng input (`print_stats.state` ->
canonical) và cùng quy tắc "webhooks.state != 'ready' -> OFFLINE" mà
`get_status()` đã dùng cho kênh HTTP - chỉ khác ở cách lấy dữ liệu
nguồn (response JSON 1 lần cho HTTP vs notification tích luỹ dần qua
WS). Phần progress/time_remaining_seconds dùng lại CÙNG công thức
(`pct > 0.02` mới tính, giống `get_status()`) nhưng viết thành hàm
riêng ở đây thay vì import, vì `get_status()` không tách hàm con cho
phần này (không sửa `http_client.py` đã khoá chỉ để tách ra cho module
này dùng).

Notification `notify_status_update` (JSON-RPC Moonraker) chỉ chứa các
object THAY ĐỔI so với lần trước, KHÔNG phải toàn bộ snapshot - class
này giữ `_raw_status` tích luỹ (merge dần từng object nhận được, kể cả
từ response ban đầu của chính `printer.objects.subscribe`, vốn đã trả
snapshot hiện tại của các object được subscribe) để luôn có đủ dữ liệu
tính canonical_status/progress/nhiệt độ, kể cả khi 1 notification chỉ
báo thay đổi ở 1 object (ví dụ chỉ `extruder` đổi nhiệt độ, không kèm
lại `print_stats`).

`fetch_printer_connection_params` đọc `(host, port, api_key)` trực
tiếp từ bảng `printers` qua `sqlite3` thô - cùng cách
`app/heartbeat/service.py` đọc dữ liệu máy, KHÔNG qua `resolve_driver`
(Quyết định 5, `app/realtime/` tách biệt hoàn toàn khỏi
`PrinterDriver`). Dùng bởi kiểm chứng thủ công của chunk này, và tái
dùng ở C3 khi dựng pool cho N máy.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp
from moonraker_api import MoonrakerClient, MoonrakerListener
from moonraker_api.const import WEBSOCKET_STATE_CONNECTED, WEBSOCKET_STATE_STOPPED

from app.db.migrate import DEFAULT_DB_PATH
from app.moonraker.http_client import (
    CANONICAL_OFFLINE,
    CANONICAL_PAUSED,
    CANONICAL_PRINTING,
    _map_print_stats_state,
)
from app.realtime.state import (
    SUBSCRIBE_OBJECTS,
    RealtimePrinterState,
    RealtimeStateStore,
)

_LOGGER = logging.getLogger(__name__)

_MIN_PROGRESS_FOR_ESTIMATE = 0.02

_SELECT_PRINTER_SQL = "SELECT ip, moonraker_port, api_key FROM printers WHERE id = ?"

def fetch_printer_connection_params(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> tuple:
    """Đọc `(host, port, api_key)` của 1 máy từ bảng `printers`.

    Raises:
        ValueError: không tìm thấy `printer_id` trong DB.
    """
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(_SELECT_PRINTER_SQL, (printer_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"Không tìm thấy máy in id={printer_id} trong DB")
    host, port, api_key = row
    return host, port, api_key

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _compute_state(raw_status: Dict[str, Dict[str, Any]]) -> RealtimePrinterState:
    """Tính `RealtimePrinterState` từ `_raw_status` đã tích luỹ - CÙNG
    quy tắc map với `http_client.py::get_status` (D-013): webhooks.state
    != 'ready' -> OFFLINE (ưu tiên hơn print_stats.state); progress/
    time_remaining chỉ tính khi progress > `_MIN_PROGRESS_FOR_ESTIMATE`.
    """
    print_stats = raw_status.get("print_stats", {})
    virtual_sdcard = raw_status.get("virtual_sdcard", {})
    webhooks = raw_status.get("webhooks", {})
    extruder = raw_status.get("extruder", {})
    heater_bed = raw_status.get("heater_bed", {})

    webhooks_state = webhooks.get("state")
    raw_print_state = print_stats.get("state")

    if webhooks_state != "ready":
        canonical_status = CANONICAL_OFFLINE
    else:
        canonical_status = _map_print_stats_state(raw_print_state)

    pct = virtual_sdcard.get("progress")
    elapsed = print_stats.get("print_duration")

    progress_percent: Optional[int] = None
    time_remaining_seconds: Optional[int] = None
    if pct is not None:
        if pct > _MIN_PROGRESS_FOR_ESTIMATE:
            progress_percent = round(pct * 100)
            if elapsed is not None:
                time_remaining_seconds = round(elapsed * (1 - pct) / pct)
        else:
            progress_percent = round(pct * 100)

    filename = None
    if canonical_status in (CANONICAL_PRINTING, CANONICAL_PAUSED):
        filename = print_stats.get("filename")

    return RealtimePrinterState(
        canonical_status=canonical_status,
        progress_percent=progress_percent,
        time_remaining_seconds=time_remaining_seconds,
        filename=filename,
        extruder_temp=_to_optional_float(extruder.get("temperature")),
        extruder_target=_to_optional_float(extruder.get("target")),
        bed_temp=_to_optional_float(heater_bed.get("temperature")),
        bed_target=_to_optional_float(heater_bed.get("target")),
        updated_at=_now_iso(),
    )

class PrinterWebsocketConnection(MoonrakerListener):
    """1 kết nối WS tới Moonraker cho 1 máy in.

    Backoff/reconnect KHÔNG nằm trong class này (thuộc `pool.py`, C3,
    Quyết định 2) - class này chỉ phơi ra `disconnected` (`asyncio.Event`)
    để caller biết KHI NÀO cần gọi lại `connect()`, không tự lặp lại.
    """

    def __init__(
        self,
        printer_id: int,
        host: str,
        port: int,
        api_key: Optional[str],
        store: RealtimeStateStore,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.printer_id = printer_id
        self.store = store
        self._raw_status: Dict[str, Dict[str, Any]] = {}
        self._connected_once = False

        self.disconnected = asyncio.Event()

        self._client = MoonrakerClient(
            listener=self, host=host, port=port, api_key=api_key, session=session
        )

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    async def connect(self) -> bool:
        """Kết nối + gửi `printer.objects.subscribe` (5 object, Quyết
        định 3). Ghi ngay state ban đầu từ response subscribe (không
        đợi notification đầu tiên - response subscribe đã trả snapshot
        hiện tại của các object được subscribe)."""
        self.disconnected.clear()
        connected = await self._client.connect()
        if not connected:
            return False
        response = await self._client.call_method(
            "printer.objects.subscribe", objects=SUBSCRIBE_OBJECTS
        )
        initial_status = (response or {}).get("status") or {}
        if initial_status:
            await self._handle_status_delta(initial_status)
        return True

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def on_notification(self, method: str, data: Any) -> None:
        """`WebsocketStatusListener` override - nhận `notify_status_update`
        (Moonraker gửi `params = [status_delta, eventtime]`), bỏ qua mọi
        notification khác (ví dụ `notify_klippy_ready`, ngoài phạm vi
        chunk này)."""
        if method != "notify_status_update":
            return
        if not data:
            return
        status_delta = data[0] if isinstance(data, list) else data
        if not isinstance(status_delta, dict):
            return
        await self._handle_status_delta(status_delta)

    async def state_changed(self, state: str) -> None:
        """`WebsocketStatusListener` override - thư viện tự gọi mỗi khi
        `WebsocketClient.state` đổi giá trị. Set `self.disconnected` khi
        rơi từ `CONNECTED` về `STOPPED` để `pool.py` (C3) biết lúc nào
        cần backoff + gọi lại `connect()` (Quyết định 2) - KHÔNG tự
        reconnect ở đây, class này chỉ báo hiệu."""
        _LOGGER.debug("Printer %s websocket state -> %s", self.printer_id, state)
        if state == WEBSOCKET_STATE_CONNECTED:
            self._connected_once = True
        elif state == WEBSOCKET_STATE_STOPPED and self._connected_once:
            self._connected_once = False
            self.disconnected.set()

    async def on_exception(self, exception: Any) -> None:
        """Chỉ log - `state_changed` (ở trên) là nơi phát hiện rớt kết
        nối để reconnect (Quyết định 2), không cần xử lý riêng ở đây vì
        thư viện luôn chuyển `state` về `STOPPED` sau mọi exception
        (xem `WebsocketClient._run`, khối `finally`)."""
        _LOGGER.warning(
            "Printer %s websocket exception: %s", self.printer_id, exception
        )

    async def _handle_status_delta(
        self, status_delta: Dict[str, Dict[str, Any]]
    ) -> None:
        """Merge object thay đổi vào `_raw_status` tích luỹ, tính lại
        `RealtimePrinterState` từ TOÀN BỘ trạng thái đã biết (không chỉ
        phần vừa nhận), rồi ghi vào `store` (C1)."""
        for obj_name, obj_fields in status_delta.items():
            if not isinstance(obj_fields, dict):
                continue
            self._raw_status.setdefault(obj_name, {}).update(obj_fields)
        state = _compute_state(self._raw_status)
        await self.store.set(self.printer_id, state)
