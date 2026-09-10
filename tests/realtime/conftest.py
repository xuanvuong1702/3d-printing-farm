
from __future__ import annotations

import asyncio
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
        asyncio.run(self._server.serve())

def _bind(host: str, port: int) -> tuple:
    simulator_app.state = SimulatorState()

    config = uvicorn.Config(simulator_app.app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = _ServerThread(server)
    thread.start()

    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Simulator không khởi động kịp trong {_STARTUP_TIMEOUT_SECONDS}s"
            )
        time.sleep(_STARTUP_POLL_INTERVAL_SECONDS)

    actual_port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, actual_port

@dataclass
class SimulatorHandle:

    host: str
    port: int
    stop: Callable[[], None]
    restart: Callable[[], None]

@pytest.fixture()
def simulator_factory() -> Iterator[Callable[..., SimulatorHandle]]:
    handles: List[SimulatorHandle] = []

    def _start(host: str = SIMULATOR_HOST) -> SimulatorHandle:
        server, thread, port = _bind(host, 0)
        state_box = {"server": server, "thread": thread, "stopped": False}

        def _stop() -> None:
            if state_box["stopped"]:
                return
            state_box["stopped"] = True
            state_box["server"].should_exit = True
            state_box["thread"].join(timeout=_STARTUP_TIMEOUT_SECONDS)

        def _restart() -> None:
            _stop()
            new_server, new_thread, _ = _bind(host, port)
            state_box["server"] = new_server
            state_box["thread"] = new_thread
            state_box["stopped"] = False

        handle = SimulatorHandle(host=host, port=port, stop=_stop, restart=_restart)
        handles.append(handle)
        return handle

    try:
        yield _start
    finally:
        for handle in handles:
            handle.stop()

@pytest.fixture()
def realtime_db_path(tmp_path) -> str:
    db_path = str(tmp_path / "test_realtime.db")
    run_migrations(db_path)
    return db_path

_INSERT_PRINTER_SQL = (
    "INSERT INTO printers (name, ip, moonraker_port, api_key) VALUES (?, ?, ?, ?)"
)

@pytest.fixture()
def insert_printer(realtime_db_path: str) -> Callable[..., int]:

    def _insert(
        ip: str,
        moonraker_port: int,
        *,
        name: str = "Realtime Test Printer",
        api_key: Optional[str] = None,
    ) -> int:
        connection = sqlite3.connect(realtime_db_path)
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL, (name, ip, moonraker_port, api_key)
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    return _insert
