
from __future__ import annotations

import asyncio
import sqlite3
from typing import Callable, Iterator, Optional

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.db.migrate import run_migrations
from app.history.service import (
    get_job_history_for_printer as _real_get_job_history_for_printer,
)
from app.realtime.schemas import PrinterRealtimeResponse
from app.realtime.service import get_printer_realtime as _real_get_printer_realtime
from app.realtime.service import list_printers_realtime as _real_list_printers_realtime
from app.realtime.state import RealtimePrinterState, RealtimeStateStore

@pytest.fixture()
def printer_detail_db_path(tmp_path) -> str:
    db_path = str(tmp_path / "test_printer_detail.db")
    run_migrations(db_path)
    return db_path

@pytest.fixture()
def client(printer_detail_db_path: str, monkeypatch) -> Iterator[TestClient]:

    async def _get_printer_realtime_with_tmp_db(
        printer_id: int, store: RealtimeStateStore
    ) -> Optional[PrinterRealtimeResponse]:
        return await _real_get_printer_realtime(
            printer_id, store, db_path=printer_detail_db_path
        )

    def _get_job_history_for_printer_with_tmp_db(
        printer_id: int, limit: int = 20
    ) -> list[dict]:
        return _real_get_job_history_for_printer(
            printer_id, db_path=printer_detail_db_path, limit=limit
        )

    async def _list_printers_realtime_with_tmp_db(store):
        return await _real_list_printers_realtime(
            store, db_path=printer_detail_db_path
        )

    monkeypatch.setattr(
        main_module, "get_printer_realtime", _get_printer_realtime_with_tmp_db
    )
    monkeypatch.setattr(
        main_module,
        "get_job_history_for_printer",
        _get_job_history_for_printer_with_tmp_db,
    )
    monkeypatch.setattr(
        main_module, "list_printers_realtime", _list_printers_realtime_with_tmp_db
    )

    with TestClient(main_module.app) as test_client:
        yield test_client

@pytest.fixture()
def set_realtime_state(client: TestClient) -> Callable[..., None]:

    def _set(
        printer_id: int,
        *,
        canonical_status: str = "PRINTING",
        progress_percent: Optional[int] = 42,
        time_remaining_seconds: Optional[int] = 120,
        filename: Optional[str] = "test.gcode",
        extruder_temp: Optional[float] = 205.0,
        extruder_target: Optional[float] = 210.0,
        bed_temp: Optional[float] = 60.0,
        bed_target: Optional[float] = 65.0,
        updated_at: str = "2026-09-15T00:00:00Z",
    ) -> None:
        state = RealtimePrinterState(
            canonical_status=canonical_status,
            progress_percent=progress_percent,
            time_remaining_seconds=time_remaining_seconds,
            filename=filename,
            extruder_temp=extruder_temp,
            extruder_target=extruder_target,
            bed_temp=bed_temp,
            bed_target=bed_target,
            updated_at=updated_at,
        )
        asyncio.run(main_module.app.state.realtime_store.set(printer_id, state))

    return _set

_INSERT_PRINTER_SQL = (
    "INSERT INTO printers (name, ip, moonraker_port, status) VALUES (?, ?, ?, ?)"
)

@pytest.fixture()
def insert_printer(printer_detail_db_path: str) -> Callable[..., int]:

    _counter = {"n": 0}

    def _insert(
        *,
        name: str = "Printer Detail Test Printer",
        ip: Optional[str] = None,
        moonraker_port: int = 7125,
        status: str = "IDLE",
    ) -> int:
        _counter["n"] += 1
        effective_ip = ip or f"127.0.1.{_counter['n']}"
        connection = sqlite3.connect(printer_detail_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL, (name, effective_ip, moonraker_port, status)
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

_INSERT_JOB_SQL = (
    "INSERT INTO jobs (printer_id, filename, status) VALUES (?, ?, ?)"
)
_INSERT_JOB_HISTORY_SQL = """
INSERT INTO job_history (
    job_id, printer_id, filename, status, start_time, end_time
) VALUES (?, ?, ?, ?, ?, ?)
"""

@pytest.fixture()
def insert_job_history(printer_detail_db_path: str) -> Callable[..., int]:

    def _insert(
        printer_id: int,
        *,
        filename: str = "test.gcode",
        status: str = "finished",
        start_time: Optional[str] = "2026-09-14T10:00:00Z",
        end_time: Optional[str] = "2026-09-14T11:00:00Z",
    ) -> int:
        connection = sqlite3.connect(printer_detail_db_path)
        try:
            job_cursor = connection.execute(
                _INSERT_JOB_SQL, (printer_id, filename, status)
            )
            job_id = job_cursor.lastrowid
            history_cursor = connection.execute(
                _INSERT_JOB_HISTORY_SQL,
                (job_id, printer_id, filename, status, start_time, end_time),
            )
            connection.commit()
            return history_cursor.lastrowid
        finally:
            connection.close()

    return _insert
