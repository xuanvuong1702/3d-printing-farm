"""
Cảnh báo lỗi máy (E2-3) — module MỚI, tách riêng khỏi
`app/realtime/state.py`/`connection.py`/`pool.py` (đã khoá từ E2-1) và
khỏi `app/realtime/service.py`/`schemas.py` (đã khoá từ E2-2), theo
đúng Quyết định phạm vi #1 (`docs/State_E2-3_v2.md`, chốt tại C0).

Phạm vi chunk E2-3/C1 (chunk này): PHẦN 1 — thuần data layer, ghi/đọc
bảng `events` (`app/db/schema.py::CREATE_EVENTS_SQL`, đã khoá từ E0-4,
CHƯA có module nào ghi vào bảng này trước E2-3). CHƯA có watcher phát
hiện transition trạng thái (C2) và CHƯA có route HTTP (C3) — 2 phần đó
sẽ import các hàm ở module này, không lặp lại logic ghi/đọc DB.

- `PrinterEventResponse`: đặt ngay trong file này (KHÔNG thêm vào
  `app/realtime/schemas.py` đã khoá) — cùng style `PrinterRealtimeResponse`
  (Pydantic `BaseModel`, field phẳng, không nested).
- `record_printer_alert_event`: INSERT 1 dòng vào `events`. Bọc
  try/except, log rồi bỏ qua nếu lỗi (Quyết định phạm vi #5) — 1 lỗi
  ghi DB (ví dụ bảng chưa tồn tại trong DB tạm lúc test, hoặc lock tạm
  thời) KHÔNG được giết hẳn watcher loop gọi hàm này ở C2, cùng tinh
  thần `_heartbeat_loop` (`app/heartbeat/scheduler.py`, đã khoá).
  `job_id` luôn để `NULL` (Quyết định phạm vi #4 — không gắn job cụ
  thể ở phạm vi MVP này), `created_at` dùng `DEFAULT` SQL của bảng,
  không truyền tay (đồng bộ quy ước timestamp đã áp dụng cho các bảng
  khác, `app/db/schema.py`).
- `list_printer_events`: SELECT lọc `printer_id` (tuỳ chọn), sắp
  `created_at DESC` (mới nhất trước), giới hạn `limit` — chặn trần ở
  `MAX_LIST_LIMIT` (200) để tránh query runaway dù caller (route C3)
  truyền giá trị lớn hơn, đúng Quyết định phạm vi #7.

Đọc/ghi DB bằng `sqlite3` thô, cùng quy ước `app/realtime/service.py`/
`app/heartbeat/service.py` đã dùng (KHÔNG qua ORM, `CLAUDE.md` mục
"Truy cập DB").
"""

from __future__ import annotations

import logging
import sqlite3
from typing import List, Optional

from pydantic import BaseModel

from app.db.migrate import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

MAX_LIST_LIMIT = 200

_INSERT_EVENT_SQL = """
INSERT INTO events (printer_id, job_id, event_type, message)
VALUES (?, NULL, ?, ?)
"""

_SELECT_EVENTS_ALL_SQL = """
SELECT id, printer_id, event_type, message, created_at
FROM events
ORDER BY created_at DESC
LIMIT ?
"""

_SELECT_EVENTS_BY_PRINTER_SQL = """
SELECT id, printer_id, event_type, message, created_at
FROM events
WHERE printer_id = ?
ORDER BY created_at DESC
LIMIT ?
"""

class PrinterEventResponse(BaseModel):
    """1 dòng của bảng `events` — trả bởi `list_printer_events` và (C3)
    bởi route `GET /printers/events`. Phản chiếu gần như 1-1 cột DB
    (khác `PrinterRealtimeResponse`, vốn là view tổng hợp) vì `events`
    đã là dữ liệu log phẳng sẵn, không cần merge thêm nguồn nào khác."""

    id: int
    printer_id: Optional[int] = None
    event_type: str
    message: Optional[str] = None
    created_at: str

def record_printer_alert_event(
    printer_id: int,
    event_type: str,
    message: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Ghi 1 dòng cảnh báo vào bảng `events` cho `printer_id`.
    `job_id` luôn `NULL` (Quyết định phạm vi #4), `created_at` dùng
    `DEFAULT` SQL của bảng (không truyền tay).

    Chịu lỗi (Quyết định phạm vi #5): nếu `INSERT` lỗi vì bất kỳ lý do
    gì (bảng chưa tồn tại, DB tạm thời lock, ...), log lại rồi bỏ qua —
    KHÔNG raise. Watcher gọi hàm này ở C2 dựa vào việc hàm không bao
    giờ làm chết vòng lặp nền của nó.
    """
    try:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                _INSERT_EVENT_SQL, (printer_id, event_type, message)
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception(
            "Lỗi ghi bảng events cho printer_id=%s, event_type=%s — bỏ qua, "
            "không chặn caller.",
            printer_id,
            event_type,
        )

def list_printer_events(
    printer_id: Optional[int] = None,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH,
) -> List[PrinterEventResponse]:
    """Đọc lại các dòng `events` gần nhất, sắp `created_at DESC` (mới
    nhất trước). `printer_id=None` (mặc định) trả về toàn bộ máy, có
    giá trị thì lọc đúng máy đó. `limit` bị chặn trần ở `MAX_LIST_LIMIT`
    (200) trước khi query, kể cả khi caller truyền giá trị lớn hơn."""
    effective_limit = min(limit, MAX_LIST_LIMIT)

    connection = sqlite3.connect(db_path)
    try:
        if printer_id is None:
            rows = connection.execute(
                _SELECT_EVENTS_ALL_SQL, (effective_limit,)
            ).fetchall()
        else:
            rows = connection.execute(
                _SELECT_EVENTS_BY_PRINTER_SQL, (printer_id, effective_limit)
            ).fetchall()
    finally:
        connection.close()

    return [
        PrinterEventResponse(
            id=row_id,
            printer_id=row_printer_id,
            event_type=event_type,
            message=message,
            created_at=created_at,
        )
        for row_id, row_printer_id, event_type, message, created_at in rows
    ]
