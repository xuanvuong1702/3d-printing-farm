
from __future__ import annotations

import asyncio
import sqlite3
from typing import Callable, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.db.migrate import run_migrations
from app.realtime.schemas import PrinterRealtimeResponse
from app.realtime.service import list_printers_realtime as _real_list_printers_realtime
from app.realtime.state import RealtimePrinterState

@pytest.fixture()
def dashboard_db_path(tmp_path) -> str:
    db_path = str(tmp_path / "test_dashboard.db")
    run_migrations(db_path)
    return db_path

@pytest.fixture()
def client(dashboard_db_path: str, monkeypatch) -> Iterator[TestClient]:

    async def _list_printers_realtime_with_tmp_db(
        store,
    ) -> List[PrinterRealtimeResponse]:
        return await _real_list_printers_realtime(store, db_path=dashboard_db_path)

    monkeypatch.setattr(
        main_module, "list_printers_realtime", _list_printers_realtime_with_tmp_db
    )

    with TestClient(main_module.app) as test_client:
        yield test_client

_INSERT_PRINTER_SQL = (
    "INSERT INTO printers (name, ip, moonraker_port, status) VALUES (?, ?, ?, ?)"
)

@pytest.fixture()
def insert_printer(dashboard_db_path: str) -> Callable[..., int]:

    _counter = {"n": 0}

    def _insert(
        *,
        name: str = "Dashboard Test Printer",
        ip: Optional[str] = None,
        moonraker_port: int = 7125,
        status: str = "IDLE",
    ) -> int:
        _counter["n"] += 1
        effective_ip = ip or f"127.0.0.{_counter['n']}"
        connection = sqlite3.connect(dashboard_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL, (name, effective_ip, moonraker_port, status)
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

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
