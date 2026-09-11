"""
Cảnh báo lỗi máy (E2-3) — module MỚI, tách riêng khỏi
`app/realtime/state.py`/`connection.py`/`pool.py` (đã khoá từ E2-1) và
khỏi `app/realtime/service.py`/`schemas.py` (đã khoá từ E2-2), theo
đúng Quyết định phạm vi #1 (`docs/State_E2-3_v2.md`, chốt tại C0).

Phạm vi chunk E2-3/C1 (đã xong): PHẦN 1 — thuần data layer, ghi/đọc
bảng `events` (`app/db/schema.py::CREATE_EVENTS_SQL`, đã khoá từ E0-4,
CHƯA có module nào ghi vào bảng này trước E2-3).

- `PrinterEventResponse`: đặt ngay trong file này (KHÔNG thêm vào
  `app/realtime/schemas.py` đã khoá) — cùng style `PrinterRealtimeResponse`
  (Pydantic `BaseModel`, field phẳng, không nested).
- `record_printer_alert_event`: INSERT 1 dòng vào `events`. Bọc
  try/except, log rồi bỏ qua nếu lỗi (Quyết định phạm vi #5) — 1 lỗi
  ghi DB (ví dụ bảng chưa tồn tại trong DB tạm lúc test, hoặc lock tạm
  thời) KHÔNG được giết hẳn watcher loop gọi hàm này (PHẦN 2 dưới),
  cùng tinh thần `_heartbeat_loop` (`app/heartbeat/scheduler.py`, đã
  khoá). `job_id` luôn để `NULL` (Quyết định phạm vi #4 — không gắn
  job cụ thể ở phạm vi MVP này), `created_at` dùng `DEFAULT` SQL của
  bảng, không truyền tay (đồng bộ quy ước timestamp đã áp dụng cho các
  bảng khác, `app/db/schema.py`).
- `list_printer_events`: SELECT lọc `printer_id` (tuỳ chọn), sắp
  `created_at DESC` (mới nhất trước), giới hạn `limit` — chặn trần ở
  `MAX_LIST_LIMIT` (200) để tránh query runaway dù caller (route C3)
  truyền giá trị lớn hơn, đúng Quyết định phạm vi #7.

Phạm vi chunk E2-3/C2 (chunk này): PHẦN 2 — watcher phát hiện
transition trạng thái, gọi lại `record_printer_alert_event` ở PHẦN 1.
CHƯA wiring vào `app/main.py` `lifespan` (C3) và CHƯA có route HTTP
`GET /printers/events` (C3).

- `ALERT_WATCH_INTERVAL_SECONDS` (Quyết định phạm vi #6): hằng số
  RIÊNG cho watcher này, KHÔNG tái dùng `REALTIME_STREAM_INTERVAL_SECONDS`
  (`app/realtime/service.py`, E2-2) hay `SCHEDULER_POLL_INTERVAL_SECONDS`
  (`app/heartbeat/scheduler.py`, E1-4) dù trùng giá trị 5.0 — 3 hằng số
  phục vụ 3 mục đích khác nhau, giữ tách biệt để sau này đổi độc lập
  không ảnh hưởng nhau.
- `_run_alert_watch_cycle`: chạy 1 vòng — đọc TOÀN BỘ
  `RealtimeStateStore.get_all()` (Quyết định phạm vi #1 — polling ĐỘC
  LẬP, KHÔNG hook vào `connection.py::store.set()`, KHÔNG đọc thêm
  bảng `printers` để lấy tên máy — `message` dùng `printer_id`, giữ
  watcher chỉ phụ thuộc đúng 1 nguồn dữ liệu đã khai báo), so sánh với
  `last_seen_status` (dict `printer_id -> canonical_status` truyền vào
  từ caller, MUTATE tại chỗ — sống xuyên suốt nhiều vòng lặp bên ngoài
  hàm này, Quyết định phạm vi #2, KHÔNG lưu vào `RealtimeStateStore`/
  DB). Với mỗi máy có `new_status != last_seen_status.get(printer_id)`
  VÀ `new_status` thuộc `_ALERT_STATUSES` (`{"ERROR", "OFFLINE"}`) —
  bao gồm CẢ lần đầu quan sát 1 `printer_id` (`.get()` trả `None`, vẫn
  tính là transition hợp lệ nếu giá trị đầu tiên đã lỗi) — gọi
  `record_printer_alert_event` với `event_type` tương ứng
  (`_EVENT_TYPE_BY_STATUS`, Quyết định phạm vi #4). KHÔNG log khi đứng
  yên ở cùng 1 trạng thái lỗi giữa 2 lần poll, KHÔNG log khi RỜI KHỎI
  `ERROR`/`OFFLINE` (Quyết định phạm vi #3). `last_seen_status` được
  cập nhật cho MỌI máy đọc được ở vòng này (kể cả máy không lỗi), để
  vòng sau so sánh đúng. Hàm `async` (khác `run_heartbeat_cycle` đồng
  bộ của E1-4) vì `RealtimeStateStore.get_all()` là coroutine
  (`asyncio.Lock` nội bộ, `state.py`, đã khoá).
- `_alert_watch_loop`/`start_alert_watcher`/`stop_alert_watcher`: tái
  dùng ĐÚNG mẫu `app/heartbeat/scheduler.py::_heartbeat_loop`/
  `start_heartbeat_scheduler`/`stop_heartbeat_scheduler` (đã khoá) —
  `asyncio.create_task`, `task.cancel()` + `await task` bọc
  `CancelledError`, lỗi bất ngờ trong 1 vòng chỉ log (`logger.exception`)
  rồi tiếp tục vòng lặp ở lần `sleep` kế tiếp, KHÔNG giết hẳn task.
  `last_seen_status` khởi tạo RỖNG bên trong `_alert_watch_loop` (1
  dict sống suốt vòng đời của task, không truyền từ ngoài vào qua
  `start_alert_watcher`) — mỗi lần watcher khởi động lại (service
  restart), toàn bộ máy coi như "chưa biết" lần nữa, đúng ý Quyết định
  phạm vi #3 ("vận hành viên cần biết máy đang lỗi ngay cả khi service
  vừa khởi động lại").

Đọc/ghi DB bằng `sqlite3` thô, cùng quy ước `app/realtime/service.py`/
`app/heartbeat/service.py` đã dùng (KHÔNG qua ORM, `CLAUDE.md` mục
"Truy cập DB").
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Dict, List, Optional

from pydantic import BaseModel

from app.db.migrate import DEFAULT_DB_PATH
from app.realtime.state import RealtimeStateStore

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

ALERT_WATCH_INTERVAL_SECONDS = 5.0

_ALERT_STATUSES = {"ERROR", "OFFLINE"}

_EVENT_TYPE_BY_STATUS = {
    "ERROR": "printer_error",
    "OFFLINE": "printer_offline",
}

async def _run_alert_watch_cycle(
    store: RealtimeStateStore,
    last_seen_status: Dict[int, str],
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Chạy 1 vòng watcher (xem docstring module cho quy tắc đầy đủ).
    `last_seen_status` bị MUTATE tại chỗ — caller (`_alert_watch_loop`)
    giữ 1 dict sống xuyên suốt nhiều lần gọi hàm này, KHÔNG tạo mới mỗi
    vòng (nếu không sẽ mất khả năng phát hiện "đứng yên"/"rời khỏi lỗi"
    giữa 2 lần poll)."""
    current_states = await store.get_all()

    for printer_id, state in current_states.items():
        new_status = state.canonical_status
        previous_status = last_seen_status.get(printer_id)

        if new_status != previous_status and new_status in _ALERT_STATUSES:
            event_type = _EVENT_TYPE_BY_STATUS[new_status]
            previous_label = previous_status or "chưa biết"
            message = f"printer_id={printer_id}: {previous_label} -> {new_status}"
            record_printer_alert_event(
                printer_id, event_type, message, db_path=db_path
            )

        last_seen_status[printer_id] = new_status

async def _alert_watch_loop(
    store: RealtimeStateStore,
    poll_interval_seconds: float,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Vòng lặp nền: gọi `_run_alert_watch_cycle` rồi nghỉ
    `poll_interval_seconds` trước lần kế tiếp. Chạy tới khi bị huỷ
    (`asyncio.CancelledError`, xem `stop_alert_watcher`). `last_seen_status`
    khởi tạo RỖNG ở đây — 1 dict sống suốt vòng đời của task (xem
    docstring module cho lý do không truyền từ ngoài vào)."""
    last_seen_status: Dict[int, str] = {}
    while True:
        try:
            await _run_alert_watch_cycle(store, last_seen_status, db_path=db_path)
        except asyncio.CancelledError:
            raise
        except Exception:

            logger.exception(
                "Lỗi không mong đợi trong 1 vòng watcher cảnh báo - tiếp "
                "tục vòng lặp ở lần kế tiếp."
            )
        await asyncio.sleep(poll_interval_seconds)

def start_alert_watcher(
    store: RealtimeStateStore,
    poll_interval_seconds: float = ALERT_WATCH_INTERVAL_SECONDS,
    db_path: str = DEFAULT_DB_PATH,
) -> "asyncio.Task[None]":
    """Khởi động task nền chạy `_alert_watch_loop`. Gọi từ FastAPI
    `lifespan` lúc startup (`app/main.py`, C3). Trả về `asyncio.Task`
    để `stop_alert_watcher` huỷ đúng lúc shutdown."""
    return asyncio.create_task(
        _alert_watch_loop(store, poll_interval_seconds, db_path=db_path)
    )

async def stop_alert_watcher(task: "asyncio.Task[None]") -> None:
    """Huỷ task nền + đợi huỷ xong hẳn (tránh warning \"Task was
    destroyed but it is pending\"). Gọi từ FastAPI `lifespan` lúc
    shutdown (`app/main.py`, C3)."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
