
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List

import pytest
import uvicorn
from fastapi.testclient import TestClient

import app.main as main_module
import app.printers.router as printers_router_module
import tools.moonraker_simulator.app as simulator_app
from app.db.migrate import run_migrations
from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT
from app.printers.service import register_printer as _real_register_printer
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
def simulator_on_default_port() -> Iterator[int]:
    simulator_app.state = SimulatorState()

    config = uvicorn.Config(
        simulator_app.app,
        host=SIMULATOR_HOST,
        port=DEFAULT_MOONRAKER_PORT,
        log_level="warning",
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

    try:
        yield DEFAULT_MOONRAKER_PORT
    finally:
        server.should_exit = True
        thread.join(timeout=_STARTUP_TIMEOUT_SECONDS)

@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    def _register_printer_with_tmp_db(request):
        return _real_register_printer(request, db_path=db_path)

    monkeypatch.setattr(
        printers_router_module, "register_printer", _register_printer_with_tmp_db
    )

    with TestClient(main_module.app) as test_client:
        yield test_client

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
