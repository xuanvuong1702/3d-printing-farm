
from __future__ import annotations

from datetime import datetime, timezone

from app.reports.service import compute_report_summary

def _iso_week(iso_timestamp: str) -> tuple:
    dt = datetime.strptime(iso_timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    iso_year, iso_week, _ = dt.isocalendar()
    return iso_year, iso_week

def test_no_data_returns_zero_none_and_empty(conn) -> None:
    result = compute_report_summary(conn, None, None, None)

    assert result.total_run_hours == 0.0
    assert result.error_rate is None
    assert result.weekly_production == []

def test_error_rate_excludes_cancelled_from_denominator(
    conn, insert_job_history
) -> None:
    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )
    insert_job_history(
        status="failed",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )
    insert_job_history(
        status="cancelled",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )

    result = compute_report_summary(conn, None, None, None)

    assert result.error_rate == 0.5

def test_error_rate_none_when_only_cancelled_entries(
    conn, insert_job_history
) -> None:
    insert_job_history(
        status="cancelled",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )

    result = compute_report_summary(conn, None, None, None)

    assert result.error_rate is None

def test_missing_time_bounds_excluded_from_total_run_hours_but_counted_in_error_rate(
    conn, insert_job_history
) -> None:
    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time=None,
    )
    insert_job_history(
        status="failed",
        start_time=None,
        end_time=None,
    )

    result = compute_report_summary(conn, None, None, None)

    assert result.total_run_hours == 0.0
    assert result.error_rate == 0.5

def test_entry_missing_only_end_time_still_counted_in_weekly_production(
    conn, insert_job_history
) -> None:
    printer_id, _ = insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time=None,
    )

    result = compute_report_summary(conn, None, None, None)

    iso_year, iso_week = _iso_week("2026-01-05T10:00:00Z")
    assert len(result.weekly_production) == 1
    entry = result.weekly_production[0]
    assert entry.printer_id == printer_id
    assert entry.iso_year == iso_year
    assert entry.iso_week == iso_week
    assert entry.finished_job_count == 1
    assert result.total_run_hours == 0.0

def test_finished_missing_start_time_excluded_from_weekly_but_counted_in_error_rate(
    conn, insert_job_history
) -> None:
    insert_job_history(
        status="finished",
        start_time=None,
        end_time="2026-01-05T11:00:00Z",
    )

    result = compute_report_summary(conn, None, None, None)

    assert result.weekly_production == []
    assert result.error_rate == 0.0
    assert result.total_run_hours == 0.0

def test_weekly_production_groups_by_printer_and_iso_week(
    conn, insert_job_history
) -> None:
    printer_a, _ = insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )
    insert_job_history(
        printer_id=printer_a,
        status="finished",
        start_time="2026-01-06T10:00:00Z",
        end_time="2026-01-06T11:00:00Z",
    )
    insert_job_history(
        printer_id=printer_a,
        status="failed",
        start_time="2026-01-06T12:00:00Z",
        end_time="2026-01-06T13:00:00Z",
    )
    printer_b, _ = insert_job_history(
        status="finished",
        start_time="2026-01-12T10:00:00Z",
        end_time="2026-01-12T11:00:00Z",
    )

    result = compute_report_summary(conn, None, None, None)

    week_a = _iso_week("2026-01-05T10:00:00Z")
    week_b = _iso_week("2026-01-12T10:00:00Z")
    entries_by_key = {
        (entry.printer_id, entry.iso_year, entry.iso_week): entry.finished_job_count
        for entry in result.weekly_production
    }
    assert entries_by_key[(printer_a, *week_a)] == 2
    assert entries_by_key[(printer_b, *week_b)] == 1
    assert len(result.weekly_production) == 2

def test_weekly_production_output_sorted_by_key(conn, insert_job_history) -> None:
    printer_b, _ = insert_job_history(
        status="finished",
        start_time="2026-01-12T10:00:00Z",
        end_time="2026-01-12T11:00:00Z",
    )
    printer_a, _ = insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )

    result = compute_report_summary(conn, None, None, None)

    keys = [
        (entry.printer_id, entry.iso_year, entry.iso_week)
        for entry in result.weekly_production
    ]
    assert keys == sorted(keys)

def test_filter_by_printer_id(conn, insert_job_history) -> None:
    printer_a, _ = insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )
    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T12:00:00Z",
    )

    result = compute_report_summary(conn, printer_a, None, None)

    assert result.total_run_hours == 1.0
    assert len(result.weekly_production) == 1
    assert result.weekly_production[0].printer_id == printer_a

def test_filter_by_since_is_inclusive(conn, insert_job_history) -> None:
    insert_job_history(
        status="finished",
        start_time="2026-01-05T00:00:00Z",
        end_time="2026-01-05T01:00:00Z",
    )
    insert_job_history(
        status="finished",
        start_time="2026-01-04T23:59:59Z",
        end_time="2026-01-05T00:59:59Z",
    )

    result = compute_report_summary(conn, None, "2026-01-05T00:00:00Z", None)

    assert result.total_run_hours == 1.0

def test_filter_by_before_is_exclusive(conn, insert_job_history) -> None:
    insert_job_history(
        status="finished",
        start_time="2026-01-06T00:00:00Z",
        end_time="2026-01-06T01:00:00Z",
    )
    insert_job_history(
        status="finished",
        start_time="2026-01-05T23:59:59Z",
        end_time="2026-01-06T00:59:59Z",
    )

    result = compute_report_summary(conn, None, None, "2026-01-06T00:00:00Z")

    assert result.total_run_hours == 1.0

def test_filter_combined_printer_id_since_before(conn, insert_job_history) -> None:
    printer_a, _ = insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )

    insert_job_history(
        printer_id=printer_a,
        status="finished",
        start_time="2026-02-01T10:00:00Z",
        end_time="2026-02-01T11:00:00Z",
    )

    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T12:00:00Z",
    )

    result = compute_report_summary(
        conn, printer_a, "2026-01-01T00:00:00Z", "2026-01-31T00:00:00Z"
    )

    assert result.total_run_hours == 1.0
    assert len(result.weekly_production) == 1
    assert result.weekly_production[0].printer_id == printer_a
