
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_NATIVE_TO_CANONICAL_STATUS = {
    "completed": "finished",
    "cancelled": "cancelled",
    "error": "failed",
    "klippy_shutdown": "failed",
    "klippy_disconnect": "failed",
    "interrupted": "failed",
}

_NATIVE_STATUS_IN_PROGRESS = "in_progress"

_SELECT_EXISTING_HISTORY_SQL = """
SELECT 1 FROM job_history
WHERE printer_id = ? AND moonraker_job_id = ?
LIMIT 1
"""

_SELECT_MATCHING_JOB_SQL = """
SELECT id FROM jobs
WHERE printer_id = ? AND filename = ? AND status = 'printing'
ORDER BY updated_at DESC
LIMIT 1
"""

_INSERT_JOB_HISTORY_SQL = """
INSERT INTO job_history (
    job_id, printer_id, filename, status,
    start_time, end_time, spool_id, moonraker_job_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPDATE_JOB_STATUS_SQL = """
UPDATE jobs
SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

def map_native_history_status(native: str) -> Optional[str]:
    if native == _NATIVE_STATUS_IN_PROGRESS:
        return None
    try:
        return _NATIVE_TO_CANONICAL_STATUS[native]
    except KeyError:
        raise ValueError(
            f"Native history status không nhận diện được: {native!r}"
        ) from None

def extract_spool_id(entry: dict) -> Optional[str]:
    for item in entry.get("auxiliary_data") or []:
        if item.get("provider") == "spoolman" and item.get("name") == "spool_ids":
            values = item.get("value") or []
            if values:
                return str(values[0])
    return None

def _unix_time_to_iso8601(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

def sync_job_history_entry(
    conn: sqlite3.Connection, printer_id: int, entry: dict[str, Any]
) -> None:
    native_status = entry.get("status")
    try:
        canonical_status = map_native_history_status(native_status)
    except ValueError:
        logger.warning(
            "Bỏ qua entry job history với native status không nhận "
            "diện được: %r (printer_id=%s, moonraker job_id=%s)",
            native_status,
            printer_id,
            entry.get("job_id"),
        )
        return

    if canonical_status is None:

        return

    if native_status != "completed" and native_status != "cancelled":

        logger.warning(
            "Job history lỗi (native status=%r map thành 'failed') "
            "printer_id=%s, moonraker job_id=%s",
            native_status,
            printer_id,
            entry.get("job_id"),
        )

    moonraker_job_id = entry.get("job_id")
    filename = entry.get("filename")

    existing = conn.execute(
        _SELECT_EXISTING_HISTORY_SQL, (printer_id, moonraker_job_id)
    ).fetchone()
    if existing is not None:

        return

    job_row = conn.execute(
        _SELECT_MATCHING_JOB_SQL, (printer_id, filename)
    ).fetchone()
    if job_row is None:

        logger.warning(
            "Không tìm thấy job nội bộ tương ứng cho entry job history "
            "(printer_id=%s, filename=%r, moonraker job_id=%s) - bỏ qua.",
            printer_id,
            filename,
            moonraker_job_id,
        )
        return

    (job_id,) = job_row
    spool_id = extract_spool_id(entry)
    start_time = _unix_time_to_iso8601(entry.get("start_time"))
    end_time = _unix_time_to_iso8601(entry.get("end_time"))

    conn.execute(
        _INSERT_JOB_HISTORY_SQL,
        (
            job_id,
            printer_id,
            filename,
            canonical_status,
            start_time,
            end_time,
            spool_id,
            moonraker_job_id,
        ),
    )
    conn.execute(_UPDATE_JOB_STATUS_SQL, (canonical_status, job_id))
    conn.commit()
