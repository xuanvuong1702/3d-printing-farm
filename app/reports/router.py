
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status

from app.db.migrate import DEFAULT_DB_PATH
from app.reports.schemas import ReportSummaryResponse
from app.reports.service import compute_report_summary

router = APIRouter()

_SELECT_PRINTER_EXISTS_SQL = "SELECT 1 FROM printers WHERE id = ?"

_ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

def _format_iso8601_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime(_ISO8601_FORMAT)

@router.get("/reports/summary", response_model=ReportSummaryResponse)
def get_report_summary(
    printer_id: Optional[int] = None,
    since: Optional[datetime] = None,
    before: Optional[datetime] = None,
) -> ReportSummaryResponse:
    connection = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        if printer_id is not None:
            exists = connection.execute(
                _SELECT_PRINTER_EXISTS_SQL, (printer_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Không tìm thấy máy in id={printer_id}.",
                )

        return compute_report_summary(
            connection,
            printer_id,
            _format_iso8601_utc(since),
            _format_iso8601_utc(before),
        )
    finally:
        connection.close()
