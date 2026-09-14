
from __future__ import annotations

import sqlite3
from typing import Callable, Iterator, Optional

import pytest

from app.db.migrate import run_migrations

@pytest.fixture()
def spools_db_path(tmp_path) -> str:
    db_path = str(tmp_path / "test_spools.db")
    run_migrations(db_path)
    return db_path

@pytest.fixture()
def conn(spools_db_path: str) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(spools_db_path)
    try:
        yield connection
    finally:
        connection.close()

_INSERT_PRINTER_SQL = """
INSERT INTO printers (
    name, ip, moonraker_port, model, api_key, klipper_version
) VALUES (?, ?, ?, ?, ?, ?)
"""

@pytest.fixture()
def insert_printer(spools_db_path: str) -> Callable[..., int]:
    _counter = {"n": 0}

    def _insert(
        *,
        name: str = "Spools Test Printer",
        ip: Optional[str] = None,
        moonraker_port: int = 7125,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        klipper_version: Optional[str] = None,
    ) -> int:
        _counter["n"] += 1
        effective_ip = ip or f"127.0.1.{_counter['n']}"
        connection = sqlite3.connect(spools_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL,
                (name, effective_ip, moonraker_port, model, api_key, klipper_version),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

@pytest.fixture()
def insert_job(
    spools_db_path: str, insert_printer: Callable[..., int]
) -> Callable[..., int]:

    def _insert(
        *,
        printer_id: Optional[int] = None,
        filename: str = "test.gcode",
        status: str = "finished",
    ) -> int:
        effective_printer_id = (
            printer_id if printer_id is not None else insert_printer()
        )
        connection = sqlite3.connect(spools_db_path)
        try:
            cursor = connection.execute(
                "INSERT INTO jobs (printer_id, filename, status) "
                "VALUES (?, ?, ?)",
                (effective_printer_id, filename, status),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

@pytest.fixture()
def insert_job_history(
    spools_db_path: str,
    insert_printer: Callable[..., int],
    insert_job: Callable[..., int],
) -> Callable[..., tuple]:

    def _insert(
        *,
        printer_id: Optional[int] = None,
        job_id: Optional[int] = None,
        filename: str = "test.gcode",
        status: str = "finished",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        spool_id: Optional[str] = None,
    ) -> tuple:
        effective_printer_id = (
            printer_id if printer_id is not None else insert_printer()
        )
        effective_job_id = (
            job_id
            if job_id is not None
            else insert_job(printer_id=effective_printer_id, filename=filename, status=status)
        )
        connection = sqlite3.connect(spools_db_path)
        try:
            cursor = connection.execute(
                "INSERT INTO job_history "
                "(job_id, printer_id, filename, status, start_time, end_time, spool_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    effective_job_id,
                    effective_printer_id,
                    filename,
                    status,
                    start_time,
                    end_time,
                    spool_id,
                ),
            )
            connection.commit()
            return effective_printer_id, cursor.lastrowid
        finally:
            connection.close()

    return _insert
