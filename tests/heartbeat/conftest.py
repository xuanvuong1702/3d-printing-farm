
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Tuple

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
def heartbeat_db_path(tmp_path) -> str:
    db_path = str(tmp_path / "test_heartbeat_service.db")
    run_migrations(db_path)
    return db_path

_INSERT_PRINTER_SQL = """
INSERT INTO printers (
    name, ip, moonraker_port, model, api_key, klipper_version,
    consecutive_heartbeat_failures, next_heartbeat_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

@pytest.fixture()
def insert_printer(heartbeat_db_path: str) -> Callable[..., int]:

    def _insert(
        ip: str,
        moonraker_port: int,
        *,
        name: str = "Heartbeat Test Printer",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        klipper_version: Optional[str] = None,
        consecutive_heartbeat_failures: int = 0,
        next_heartbeat_at: Optional[str] = None,
    ) -> int:
        connection = sqlite3.connect(heartbeat_db_path)
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
                    consecutive_heartbeat_failures,
                    next_heartbeat_at,
                ),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert

@pytest.fixture()
def fetch_printer(heartbeat_db_path: str) -> Callable[[int], Tuple]:

    def _fetch(printer_id: int) -> Tuple:
        connection = sqlite3.connect(heartbeat_db_path)
        try:
            row = connection.execute(
                "SELECT status, consecutive_heartbeat_failures, "
                "next_heartbeat_at FROM printers WHERE id = ?",
                (printer_id,),
            ).fetchone()
        finally:
            connection.close()
        return row

    return _fetch

@pytest.fixture()
def set_next_heartbeat_at(heartbeat_db_path: str) -> Callable[[int, str], None]:

    def _set(printer_id: int, next_heartbeat_at: str) -> None:
        connection = sqlite3.connect(heartbeat_db_path)
        try:
            connection.execute(
                "UPDATE printers SET next_heartbeat_at = ? WHERE id = ?",
                (next_heartbeat_at, printer_id),
            )
            connection.commit()
        finally:
            connection.close()

    return _set
