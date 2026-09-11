
from __future__ import annotations

import asyncio
import json
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

from tests.printers.conftest import client

__all__ = ["client"]

SIMULATOR_HOST = "127.0.0.1"
_STARTUP_TIMEOUT_SECONDS = 5.0
_STARTUP_POLL_INTERVAL_SECONDS = 0.05

_NO_JOB_QUEUE_CAPABILITIES = ["klippy_connection", "file_manager"]

class _ServerThread(threading.Thread):

    def __init__(self, server: uvicorn.Server) -> None:
        super().__init__(daemon=True)
        self._server = server

    def run(self) -> None:
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
def dispatch_db_path(tmp_path) -> str:
    db_path = str(tmp_path / "test_dispatch_service.db")
    run_migrations(db_path)
    return db_path

_INSERT_PRINTER_SQL = """
INSERT INTO printers (
    name, ip, moonraker_port, model, api_key, klipper_version,
    capabilities, status, is_held
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

@pytest.fixture()
def insert_printer(dispatch_db_path: str) -> Callable[..., int]:

    def _insert(
        ip: str,
        moonraker_port: int,
        *,
        name: str = "Dispatch Test Printer",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        klipper_version: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        status: str = "IDLE",
        is_held: int = 0,
    ) -> int:
        capabilities_json = json.dumps(
            capabilities if capabilities is not None else _NO_JOB_QUEUE_CAPABILITIES
        )
        connection = sqlite3.connect(dispatch_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL,
                (
                    name,
                    ip,
                    moonraker_port,
                    model,
                    api_key,
                    klipper_version,
                    capabilities_json,
                    status,
                    is_held,
                ),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

_INSERT_JOB_SQL = """
INSERT INTO jobs (printer_id, filename, status, priority, created_at)
VALUES (?, ?, ?, ?, ?)
"""

@pytest.fixture()
def insert_job(dispatch_db_path: str) -> Callable[..., int]:
    _counter = {"n": 0}

    def _insert(
        printer_id: int,
        filename: str,
        *,
        status: str = "queued",
        priority: int = 0,
        created_at: Optional[str] = None,
    ) -> int:
        _counter["n"] += 1
        effective_created_at = created_at or f"2026-01-01T00:00:{_counter['n']:02d}Z"
        connection = sqlite3.connect(dispatch_db_path)
        try:
            cursor = connection.execute(
                _INSERT_JOB_SQL,
                (printer_id, filename, status, priority, effective_created_at),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

@pytest.fixture()
def fetch_job_status(dispatch_db_path: str) -> Callable[[int], str]:

    def _fetch(job_id: int) -> str:
        connection = sqlite3.connect(dispatch_db_path)
        try:
            (status,) = connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        finally:
            connection.close()
        return status

    return _fetch
