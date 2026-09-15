
from __future__ import annotations

import sqlite3
from typing import Callable, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.db.migrate import run_migrations
from app.realtime.schemas import PrinterRealtimeResponse
from app.realtime.service import list_printers_realtime as _real_list_printers_realtime

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
