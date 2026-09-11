
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
