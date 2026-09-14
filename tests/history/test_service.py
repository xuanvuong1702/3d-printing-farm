
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import pytest

import app.history.service as service_module
from app.history.service import (
    extract_spool_id,
    map_native_history_status,
    run_history_sync_cycle,
    sync_job_history_entry,
)
from app.moonraker.http_client import MoonrakerClientError

@pytest.mark.parametrize(
    "native, expected_canonical",
    [
        ("completed", "finished"),
        ("cancelled", "cancelled"),
        ("error", "failed"),
        ("klippy_shutdown", "failed"),
        ("klippy_disconnect", "failed"),
        ("interrupted", "failed"),
        ("in_progress", None),
    ],
)
def test_map_native_history_status_known_values(native, expected_canonical) -> None:
    assert map_native_history_status(native) == expected_canonical

def test_map_native_history_status_unknown_raises_value_error() -> None:
    with pytest.raises(ValueError):
        map_native_history_status("some_unrecognized_native_status")

def test_extract_spool_id_returns_first_value_when_spoolman_present() -> None:
    entry = {
        "auxiliary_data": [
            {"provider": "spoolman", "name": "spool_ids", "value": [42, 7]}
        ]
    }
    assert extract_spool_id(entry) == "42"

def test_extract_spool_id_returns_none_without_spoolman_element() -> None:
    entry = {
        "auxiliary_data": [{"provider": "other_component", "name": "x", "value": [1]}]
    }
    assert extract_spool_id(entry) is None

def test_extract_spool_id_returns_none_when_value_array_empty() -> None:
    entry = {
        "auxiliary_data": [{"provider": "spoolman", "name": "spool_ids", "value": []}]
    }
    assert extract_spool_id(entry) is None

def test_extract_spool_id_returns_none_without_auxiliary_data() -> None:
    assert extract_spool_id({}) is None

def _make_entry(
    *,
    job_id: str = "moonraker-job-1",
    filename: str = "print1.gcode",
    status: str = "completed",
    start_time: float = 1000.0,
    end_time: float = 2000.0,
    auxiliary_data=None,
) -> dict:
    return {
        "job_id": job_id,
        "filename": filename,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "auxiliary_data": auxiliary_data or [],
    }

def test_sync_job_history_entry_inserts_and_updates_job_status(
    history_db_path, insert_printer, insert_job, fetch_job_status, fetch_job_history_rows
) -> None:
    printer_id = insert_printer("10.0.1.1", 7125)
    job_id = insert_job(printer_id, "print1.gcode", status="printing")
    entry = _make_entry(
        auxiliary_data=[
            {"provider": "spoolman", "name": "spool_ids", "value": ["42"]}
        ]
    )

    connection = sqlite3.connect(history_db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        sync_job_history_entry(connection, printer_id, entry)
    finally:
        connection.close()

    assert fetch_job_status(job_id) == "finished"
    rows = fetch_job_history_rows(printer_id)
    assert len(rows) == 1
    (
        row_job_id,
        row_filename,
        row_status,
        row_start_time,
        row_end_time,
        row_spool_id,
        row_moonraker_job_id,
    ) = rows[0]
    assert row_job_id == job_id
    assert row_filename == "print1.gcode"
    assert row_status == "finished"
    assert row_start_time == datetime.fromtimestamp(1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert row_end_time == datetime.fromtimestamp(2000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert row_spool_id == "42"
    assert row_moonraker_job_id == "moonraker-job-1"

def test_sync_job_history_entry_in_progress_is_skipped(
    history_db_path, insert_printer, insert_job, fetch_job_status, fetch_job_history_rows
) -> None:
    printer_id = insert_printer("10.0.1.2", 7125)
    job_id = insert_job(printer_id, "print2.gcode", status="printing")
    entry = _make_entry(filename="print2.gcode", status="in_progress")

    connection = sqlite3.connect(history_db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        sync_job_history_entry(connection, printer_id, entry)
    finally:
        connection.close()

    assert fetch_job_status(job_id) == "printing"
    assert fetch_job_history_rows(printer_id) == []

def test_sync_job_history_entry_is_idempotent_on_moonraker_job_id(
    history_db_path, insert_printer, insert_job, fetch_job_history_rows
) -> None:
    printer_id = insert_printer("10.0.1.3", 7125)
    insert_job(printer_id, "print3.gcode", status="printing")
    entry = _make_entry(job_id="moonraker-job-dup", filename="print3.gcode")

    connection = sqlite3.connect(history_db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        sync_job_history_entry(connection, printer_id, entry)
        sync_job_history_entry(connection, printer_id, entry)
    finally:
        connection.close()

    assert len(fetch_job_history_rows(printer_id)) == 1

def test_sync_job_history_entry_without_matching_job_is_skipped(
    history_db_path, insert_printer, fetch_job_history_rows
) -> None:
    printer_id = insert_printer("10.0.1.4", 7125)
    entry = _make_entry(filename="no_matching_job.gcode")

    connection = sqlite3.connect(history_db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        sync_job_history_entry(connection, printer_id, entry)
    finally:
        connection.close()

    assert fetch_job_history_rows(printer_id) == []

def test_sync_job_history_entry_unknown_native_status_logs_warning_and_skips(
    history_db_path,
    insert_printer,
    insert_job,
    fetch_job_status,
    fetch_job_history_rows,
    caplog,
) -> None:
    printer_id = insert_printer("10.0.1.5", 7125)
    job_id = insert_job(printer_id, "print5.gcode", status="printing")
    entry = _make_entry(filename="print5.gcode", status="some_unrecognized_status")

    connection = sqlite3.connect(history_db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with caplog.at_level(logging.WARNING, logger="app.history.service"):
            sync_job_history_entry(connection, printer_id, entry)
    finally:
        connection.close()

    assert fetch_job_status(job_id) == "printing"
    assert fetch_job_history_rows(printer_id) == []
    assert any(
        "không nhận diện được" in record.message for record in caplog.records
    )

class _FakeDriver:

    def __init__(self, *, response=None, error=None, since_calls=None):
        self._response = response if response is not None else {"count": 0, "jobs": []}
        self._error = error
        self._since_calls = since_calls if since_calls is not None else []

    def get_history_list(self, limit: int = 50, since=None) -> dict:
        self._since_calls.append(since)
        if self._error is not None:
            raise self._error
        return self._response

def _patch_resolve_driver(monkeypatch, drivers_by_ip: dict) -> None:

    def _fake_resolve_driver(*, model, firmware_version, host, port, api_key):
        return drivers_by_ip[host]

    monkeypatch.setattr(service_module, "resolve_driver", _fake_resolve_driver)

def test_run_history_sync_cycle_first_sync_uses_since_none(
    history_db_path, insert_printer, monkeypatch
) -> None:
    printer_id = insert_printer("10.0.2.1", 7125)
    since_calls: list = []
    fake_driver = _FakeDriver(since_calls=since_calls)
    _patch_resolve_driver(monkeypatch, {"10.0.2.1": fake_driver})

    run_history_sync_cycle(db_path=history_db_path)

    assert since_calls == [None]
    assert printer_id

def test_run_history_sync_cycle_computes_since_from_latest_history_created_at(
    history_db_path, insert_printer, insert_job, monkeypatch
) -> None:
    printer_id = insert_printer("10.0.2.2", 7125)
    job_id = insert_job(printer_id, "already_synced.gcode", status="finished")

    connection = sqlite3.connect(history_db_path)
    try:
        connection.execute(
            "INSERT INTO job_history (job_id, printer_id, filename, status, "
            "moonraker_job_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                job_id,
                printer_id,
                "already_synced.gcode",
                "finished",
                "moonraker-old-1",
                "2026-01-01T00:00:00Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    since_calls: list = []
    fake_driver = _FakeDriver(since_calls=since_calls)
    _patch_resolve_driver(monkeypatch, {"10.0.2.2": fake_driver})

    run_history_sync_cycle(db_path=history_db_path)

    expected_since = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert since_calls == [expected_since]

def test_run_history_sync_cycle_error_on_one_printer_does_not_stop_others(
    history_db_path, insert_printer, insert_job, fetch_job_status, monkeypatch
) -> None:
    failing_printer_id = insert_printer("10.0.2.3", 7125)
    insert_job(failing_printer_id, "will_error.gcode", status="printing")

    ok_printer_id = insert_printer("10.0.2.4", 7125)
    ok_job_id = insert_job(ok_printer_id, "will_succeed.gcode", status="printing")

    failing_driver = _FakeDriver(error=MoonrakerClientError("simulated network error"))
    ok_entry = _make_entry(
        job_id="moonraker-job-ok", filename="will_succeed.gcode", status="completed"
    )
    ok_driver = _FakeDriver(response={"count": 1, "jobs": [ok_entry]})
    _patch_resolve_driver(
        monkeypatch, {"10.0.2.3": failing_driver, "10.0.2.4": ok_driver}
    )

    run_history_sync_cycle(db_path=history_db_path)

    assert fetch_job_status(ok_job_id) == "finished"
