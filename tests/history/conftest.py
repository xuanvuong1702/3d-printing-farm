
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional

import pytest
import uvicorn

import tools.moonraker_simulator.app as simulator_app
from app.db.migrate import run_migrations
from tools.moonraker_simulator.state import SimulatorState

SIMULATOR_HOST = "127.0.0.1"
_STARTUP_TIMEOUT_SECONDS = 5.0
_STARTUP_POLL_INTERVAL_SECONDS = 0.05

class _ServerThread(threading.Thread):

    def __init__(self, server: uvicorn.Server) -> None:
        super().__init__(daemon=True)
        self._server = server

    def run(self) -> None:
        import asyncio

        asyncio.run(self._server.serve())

@dataclass
class SimulatorHandle:

    host: str
    port: int
    stop: Callable[[], None]

@pytest.fixture()
def simulator_factory() -> Iterator[Callable[..., SimulatorHandle]]:
    handles: List[SimulatorHandle] = []

    def _start(host: str = SIMULATOR_HOST) -> SimulatorHandle:
        simulator_app.state = SimulatorState()

        config = uvicorn.Config(
            simulator_app.app, host=host, port=0, log_level="warning"
        )
        server = uvicorn.Server(config)
        thread = _ServerThread(server)
        thread.start()

        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Simulator không khởi động kịp trong "
                    f"{_STARTUP_TIMEOUT_SECONDS}s"
                )
            time.sleep(_STARTUP_POLL_INTERVAL_SECONDS)

        port = server.servers[0].sockets[0].getsockname()[1]
        stopped = False

        def _stop() -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            server.should_exit = True
            thread.join(timeout=_STARTUP_TIMEOUT_SECONDS)

        handle = SimulatorHandle(host=host, port=port, stop=_stop)
        handles.append(handle)
        return handle

    try:
        yield _start
    finally:
        for handle in handles:
            handle.stop()

@pytest.fixture()
def simulator() -> Iterator[int]:
    simulator_app.state = SimulatorState()

    config = uvicorn.Config(
        simulator_app.app, host=SIMULATOR_HOST, port=0, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = _ServerThread(server)
    thread.start()

    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Simulator không khởi động kịp trong "
                f"{_STARTUP_TIMEOUT_SECONDS}s"
            )
        time.sleep(_STARTUP_POLL_INTERVAL_SECONDS)

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=_STARTUP_TIMEOUT_SECONDS)

@pytest.fixture()
def history_db_path(tmp_path) -> str:
    db_path = str(tmp_path / "test_history_service.db")
    run_migrations(db_path)
    return db_path

_INSERT_PRINTER_SQL = """
INSERT INTO printers (
    name, ip, moonraker_port, model, api_key, klipper_version
) VALUES (?, ?, ?, ?, ?, ?)
"""

@pytest.fixture()
def insert_printer(history_db_path: str) -> Callable[..., int]:

    def _insert(
        ip: str,
        moonraker_port: int,
        *,
        name: str = "History Test Printer",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        klipper_version: Optional[str] = None,
    ) -> int:
        connection = sqlite3.connect(history_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL,
                (name, ip, moonraker_port, model, api_key, klipper_version),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

@pytest.fixture()
def insert_job(history_db_path: str) -> Callable[..., int]:
    _counter = {"n": 0}

    def _insert(
        printer_id: int,
        filename: str,
        *,
        status: str = "printing",
        updated_at: Optional[str] = None,
    ) -> int:
        _counter["n"] += 1
        effective_updated_at = updated_at or f"2026-01-01T00:00:{_counter['n']:02d}Z"
        connection = sqlite3.connect(history_db_path)
        try:
            cursor = connection.execute(
                "INSERT INTO jobs (printer_id, filename, status, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (printer_id, filename, status, effective_updated_at),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

@pytest.fixture()
def fetch_job_status(history_db_path: str) -> Callable[[int], str]:

    def _fetch(job_id: int) -> str:
        connection = sqlite3.connect(history_db_path)
        try:
            (status,) = connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        finally:
            connection.close()
        return status

    return _fetch

@pytest.fixture()
def fetch_job_history_rows(history_db_path: str) -> Callable[[int], List[tuple]]:

    def _fetch(printer_id: int) -> List[tuple]:
        connection = sqlite3.connect(history_db_path)
        try:
            rows = connection.execute(
                "SELECT job_id, filename, status, start_time, end_time, "
                "spool_id, moonraker_job_id FROM job_history "
                "WHERE printer_id = ? ORDER BY id ASC",
                (printer_id,),
            ).fetchall()
        finally:
            connection.close()
        return rows

    return _fetch
