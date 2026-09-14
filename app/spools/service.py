
from __future__ import annotations

import sqlite3

from app.spools.schemas import SpoolJobEntry, SpoolReport

_SELECT_SPOOL_JOBS_SQL = (
    "SELECT id, job_id, filename, status, start_time "
    "FROM job_history "
    "WHERE spool_id = ? AND printer_id = ? "
    "ORDER BY id"
)

def build_spool_report(
    conn: sqlite3.Connection,
    printer_id: int,
    spool_id: str,
    spoolman_response: dict,
) -> SpoolReport:
    response = spoolman_response.get("response") or {}
    filament = response.get("filament") or {}

    rows = conn.execute(
        _SELECT_SPOOL_JOBS_SQL, (spool_id, printer_id)
    ).fetchall()
    jobs = [
        SpoolJobEntry(
            job_history_id=row_id,
            job_id=job_id,
            filename=filename,
            status=status,
            start_time=start_time,
        )
        for row_id, job_id, filename, status, start_time in rows
    ]

    return SpoolReport(
        spool_id=spool_id,
        printer_id=printer_id,
        material=filament.get("material"),
        filament_name=filament.get("name"),
        used_weight_g=response.get("used_weight"),
        remaining_weight_g=response.get("remaining_weight"),
        jobs=jobs,
    )
