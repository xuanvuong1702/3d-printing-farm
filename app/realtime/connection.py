"""
Kết nối WebSocket cho 1 máy in (E2-1/C2) - wrapper quanh
`MoonrakerClient`/`MoonrakerListener` (thư viện `moonraker-api`, D-002
phần 2).

Phạm vi chunk này (`docs/State_E2-1_v3.md` mục "CHUNK KẾ TIẾP CẦN
CHẠY"): quản lý ĐÚNG 1 kết nối WS - `connect()` + gửi
`printer.objects.subscribe` (`SUBSCRIBE_OBJECTS`, 5 object, Quyết định
3, `app/realtime/state.py`), nhận notification + map -> ghi vào
`RealtimeStateStore` (đã có từ C1). CHƯA có backoff/reconnect khi rớt
kết nối (Quyết định 2, thuộc C3) - `state_changed`/`on_exception` ở
đây là no-op có chủ đích (chỉ log), KHÔNG tự gọi lại `connect()`.

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

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from moonraker_api import MoonrakerClient, MoonrakerListener

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
    """1 kết nối WS tới Moonraker cho 1 máy in. CHƯA có backoff/reconnect
    (thuộc C3) - nếu kết nối rớt, `state_changed`/`on_exception` chỉ
    log, KHÔNG tự gọi lại `connect()`."""

    def __init__(
        self,
        printer_id: int,
        host: str,
        port: int,
        api_key: Optional[str],
        store: RealtimeStateStore,
    ) -> None:
        self.printer_id = printer_id
        self.store = store
        self._raw_status: Dict[str, Dict[str, Any]] = {}
        self._client = MoonrakerClient(listener=self, host=host, port=port, api_key=api_key)

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    async def connect(self) -> bool:
        """Kết nối + gửi `printer.objects.subscribe` (5 object, Quyết
        định 3). Ghi ngay state ban đầu từ response subscribe (không
        đợi notification đầu tiên - response subscribe đã trả snapshot
        hiện tại của các object được subscribe)."""
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
        """No-op có chủ đích ở chunk này (chỉ log) - reconnect thuộc C3
        (Quyết định 2)."""
        _LOGGER.debug("Printer %s websocket state -> %s", self.printer_id, state)

    async def on_exception(self, exception: Any) -> None:
        """No-op có chủ đích ở chunk này (chỉ log) - reconnect thuộc C3
        (Quyết định 2)."""
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
