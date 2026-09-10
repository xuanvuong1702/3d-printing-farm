"""
Logic merge dữ liệu bảng `printers` (định danh máy + trạng thái
fallback từ heartbeat, E1-4) với `RealtimeStateStore` (dữ liệu WS
real-time, đã khoá từ E2-1) thành `PrinterRealtimeResponse`
(`app/realtime/schemas.py`) — phục vụ cả 3 endpoint của
`app/realtime/router.py` (C2/C3, chưa viết ở chunk này).

Đọc bảng `printers` bằng `sqlite3` thô, cùng quy ước
`app/printers/service.py` đã dùng (KHÔNG qua ORM, `CLAUDE.md` mục
"Truy cập DB") — module này KHÔNG bao giờ ghi vào bảng `printers`
(chỉ đọc `id`/`name`/`status`), tránh đụng vào cột `status` mà
`app/printers/service.py::list_printers`/`app/heartbeat/service.py` đã
là chủ sở hữu ghi (write-through, D-013) — real-time API chỉ là READER
của dữ liệu đó cho mục đích fallback, không phải writer thứ 2.

Quyết định phạm vi #4 (`docs/State_E2-2_v2.md`, chốt tại C0) áp dụng
tại `_merge_row_with_realtime_state` — xem docstring hàm đó.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from app.db.migrate import DEFAULT_DB_PATH
from app.realtime.schemas import PrinterRealtimeResponse
from app.realtime.state import RealtimePrinterState, RealtimeStateStore

_SELECT_PRINTER_ID_NAME_STATUS_SQL = """
SELECT id, name, status FROM printers WHERE id = ?
"""

_SELECT_ALL_PRINTER_ID_NAME_STATUS_SQL = """
SELECT id, name, status FROM printers
"""

def _merge_row_with_realtime_state(
    printer_id: int,
    name: str,
    db_status: str,
    realtime_state: Optional[RealtimePrinterState],
) -> PrinterRealtimeResponse:
    """Merge 1 dòng `(id, name, status)` của bảng `printers` với
    `RealtimePrinterState` (`None` nếu `RealtimeStateStore` chưa từng
    nhận notification cho máy này — Quyết định phạm vi #4):

    - `realtime_state is None` -> `realtime_connected=False`,
      `canonical_status` FALLBACK về `db_status` (cột `printers.status`
      DB, nguồn heartbeat E1-4), mọi field còn lại `None`.
    - `realtime_state` có giá trị -> `realtime_connected=True`,
      `canonical_status` lấy từ `realtime_state.canonical_status`
      (nguồn WS, ưu tiên hơn DB vì mới/chi tiết hơn, D-013 - KHÔNG map
      lại giá trị), copy nguyên 7 field còn lại.
    """
    if realtime_state is None:
        return PrinterRealtimeResponse(
            id=printer_id,
            name=name,
            realtime_connected=False,
            canonical_status=db_status,
        )

    return PrinterRealtimeResponse(
        id=printer_id,
        name=name,
        realtime_connected=True,
        canonical_status=realtime_state.canonical_status,
        progress_percent=realtime_state.progress_percent,
        time_remaining_seconds=realtime_state.time_remaining_seconds,
        filename=realtime_state.filename,
        extruder_temp=realtime_state.extruder_temp,
        extruder_target=realtime_state.extruder_target,
        bed_temp=realtime_state.bed_temp,
        bed_target=realtime_state.bed_target,
        updated_at=realtime_state.updated_at,
    )

async def get_printer_realtime(
    printer_id: int,
    store: RealtimeStateStore,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PrinterRealtimeResponse]:
    """Snapshot real-time của 1 máy. Trả `None` nếu `printer_id` không
    tồn tại trong bảng `printers` (router map sang HTTP 404, C2) — khác
    biệt tường minh với "tồn tại nhưng chưa có dữ liệu realtime"
    (`realtime_connected=False`, vẫn trả response bình thường)."""
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            _SELECT_PRINTER_ID_NAME_STATUS_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    db_id, name, db_status = row
    realtime_state = await store.get(db_id)
    return _merge_row_with_realtime_state(db_id, name, db_status, realtime_state)

async def list_printers_realtime(
    store: RealtimeStateStore,
    db_path: str = DEFAULT_DB_PATH,
) -> List[PrinterRealtimeResponse]:
    """Snapshot real-time của TOÀN BỘ máy đã đăng ký — dùng cho
    `GET /printers/realtime` (state khởi tạo dashboard) và làm nền cho
    `GET /printers/realtime/stream` (đẩy định kỳ, C3)."""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(_SELECT_ALL_PRINTER_ID_NAME_STATUS_SQL).fetchall()
    finally:
        connection.close()

    all_states = await store.get_all()

    return [
        _merge_row_with_realtime_state(
            printer_id, name, db_status, all_states.get(printer_id)
        )
        for printer_id, name, db_status in rows
    ]
