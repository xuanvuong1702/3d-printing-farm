
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app.reports.schemas import ReportSummaryResponse, WeeklyProductionEntry

logger = logging.getLogger(__name__)

def _iso8601_to_unix_time(value: str) -> float:
    return (
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )

def _select_history_rows_sql(
    printer_id: Optional[int], since: Optional[str], before: Optional[str]
) -> tuple[str, tuple]:
    conditions = []
    params: list = []

    if printer_id is not None:
        conditions.append("printer_id = ?")
        params.append(printer_id)
    if since is not None:
        conditions.append("start_time >= ?")
        params.append(since)
    if before is not None:
        conditions.append("start_time < ?")
        params.append(before)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        "SELECT printer_id, status, start_time, end_time "
        f"FROM job_history {where_clause}"
    ).strip()
    return sql, tuple(params)

def compute_report_summary(
    conn: sqlite3.Connection,
    printer_id: Optional[int],
    since: Optional[str],
    before: Optional[str],
) -> ReportSummaryResponse:
    sql, params = _select_history_rows_sql(printer_id, since, before)
    rows = conn.execute(sql, params).fetchall()

    total_run_seconds = 0.0
    missing_time_bounds_count = 0
    failed_count = 0
    finished_count = 0

    weekly_counts: dict[tuple[int, int, int], int] = {}
    missing_start_time_finished_count = 0

    for row_printer_id, status, start_time, end_time in rows:
        if status == "failed":
            failed_count += 1
        elif status == "finished":
            finished_count += 1

        if start_time is not None and end_time is not None:
            total_run_seconds += _iso8601_to_unix_time(
                end_time
            ) - _iso8601_to_unix_time(start_time)
        else:
            missing_time_bounds_count += 1

        if status == "finished":
            if start_time is None:
                missing_start_time_finished_count += 1
            else:
                dt = datetime.strptime(
                    start_time, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
                iso_year, iso_week, _ = dt.isocalendar()
                key = (row_printer_id, iso_year, iso_week)
                weekly_counts[key] = weekly_counts.get(key, 0) + 1

    if missing_time_bounds_count:
        logger.warning(
            "%d entry job_history bị loại khỏi total_run_hours vì "
            "thiếu start_time hoặc end_time (printer_id filter=%s, "
            "since=%s, before=%s).",
            missing_time_bounds_count,
            printer_id,
            since,
            before,
        )
    if missing_start_time_finished_count:
        logger.warning(
            "%d entry job_history status='finished' bị loại khỏi "
            "weekly_production vì thiếu start_time (printer_id "
            "filter=%s, since=%s, before=%s).",
            missing_start_time_finished_count,
            printer_id,
            since,
            before,
        )

    error_rate: Optional[float]
    denominator = failed_count + finished_count
    error_rate = None if denominator == 0 else failed_count / denominator

    weekly_production = [
        WeeklyProductionEntry(
            printer_id=key_printer_id,
            iso_year=iso_year,
            iso_week=iso_week,
            finished_job_count=count,
        )
        for (key_printer_id, iso_year, iso_week), count in sorted(
            weekly_counts.items()
        )
    ]

    return ReportSummaryResponse(
        total_run_hours=round(total_run_seconds / 3600, 2),
        error_rate=error_rate,
        weekly_production=weekly_production,
    )
