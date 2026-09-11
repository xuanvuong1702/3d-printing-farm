
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
    return asyncio.create_task(
        _alert_watch_loop(store, poll_interval_seconds, db_path=db_path)
    )

async def stop_alert_watcher(task: "asyncio.Task[None]") -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
