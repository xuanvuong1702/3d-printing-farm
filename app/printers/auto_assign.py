
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from app.db.migrate import DEFAULT_DB_PATH
from app.printers.service import (
    enqueue_job_to_moonraker_queue,
    upload_file_to_printer,
)
from app.realtime.state import RealtimeStateStore

@dataclass(frozen=True)
class IdleCandidate:

    printer_id: int
    queue_length: int

@dataclass(frozen=True)
class PrintingCandidate:

    printer_id: int
    time_remaining_seconds: Optional[int]

class NoAvailablePrinterError(Exception):
    pass

def select_auto_assign_printer(
    idle_candidates: List[IdleCandidate],
    printing_candidates: List[PrintingCandidate],
) -> Optional[int]:
    if idle_candidates:
        best_idle = min(
            idle_candidates,
            key=lambda candidate: (candidate.queue_length, candidate.printer_id),
        )
        return best_idle.printer_id

    if printing_candidates:
        best_printing = min(
            printing_candidates,
            key=lambda candidate: (
                candidate.time_remaining_seconds is None,
                (
                    candidate.time_remaining_seconds
                    if candidate.time_remaining_seconds is not None
                    else 0
                ),
                candidate.printer_id,
            ),
        )
        return best_printing.printer_id

    return None

_SELECT_IDLE_CANDIDATES_SQL = """
SELECT
    p.id,
    (
        SELECT COUNT(*) FROM jobs j
        WHERE j.printer_id = p.id AND j.status = 'queued'
    ) AS queue_length
FROM printers p
WHERE p.status = 'IDLE' AND p.is_held = 0
"""

_SELECT_PRINTING_CANDIDATES_SQL = """
SELECT id
FROM printers
WHERE status = 'PRINTING' AND is_held = 0
"""

async def auto_assign_and_upload(
    filename: str,
    file_content: bytes,
    store: RealtimeStateStore,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        idle_rows = connection.execute(_SELECT_IDLE_CANDIDATES_SQL).fetchall()
        printing_rows = connection.execute(_SELECT_PRINTING_CANDIDATES_SQL).fetchall()
    finally:
        connection.close()

    idle_candidates = [
        IdleCandidate(printer_id=printer_id, queue_length=queue_length)
        for printer_id, queue_length in idle_rows
    ]

    realtime_states = await store.get_all()
    printing_candidates = [
        PrintingCandidate(
            printer_id=printer_id,
            time_remaining_seconds=(
                realtime_states[printer_id].time_remaining_seconds
                if printer_id in realtime_states
                else None
            ),
        )
        for (printer_id,) in printing_rows
    ]

    selected_printer_id = select_auto_assign_printer(
        idle_candidates, printing_candidates
    )
    if selected_printer_id is None:
        raise NoAvailablePrinterError(
            "Không có máy nào đủ điều kiện nhận job qua auto-assign "
            "(cả tier IDLE lẫn tier PRINTING đều rỗng)."
        )

    job = upload_file_to_printer(
        printer_id=selected_printer_id,
        filename=filename,
        file_content=file_content,
        db_path=db_path,
    )
    if job is None:
        raise NoAvailablePrinterError(
            f"Máy id={selected_printer_id} vừa được chọn nhưng không "
            "còn tồn tại lúc upload (race condition hiếm gặp)."
        )

    enqueue_job_to_moonraker_queue(
        printer_id=selected_printer_id,
        filename=filename,
        db_path=db_path,
    )

    return job
