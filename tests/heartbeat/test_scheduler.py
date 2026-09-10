
from __future__ import annotations

import asyncio
import sqlite3
import time
import warnings

import app.heartbeat.scheduler as scheduler_module

def test_start_and_stop_scheduler_cancels_cleanly_without_warnings(monkeypatch):
    call_count = 0

    def _stub_run_heartbeat_cycle() -> None:
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(scheduler_module, "run_heartbeat_cycle", _stub_run_heartbeat_cycle)

    async def _scenario() -> "asyncio.Task[None]":
        task = scheduler_module.start_heartbeat_scheduler(poll_interval_seconds=0.05)

        await asyncio.sleep(0.3)
        await scheduler_module.stop_heartbeat_scheduler(task)
        return task

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        task = asyncio.run(_scenario())

    assert task.cancelled()
    assert call_count >= 1

def test_lifespan_wires_real_background_scheduler_updates_next_heartbeat(
    client, simulator_factory, tmp_path
):
    handle = simulator_factory(host="127.0.0.1")
    register_response = client.post(
        "/printers",
        json={
            "name": "Scheduler Wiring Printer",
            "ip": handle.host,
            "moonraker_port": handle.port,
        },
    )
    assert register_response.status_code == 201
    printer_id = register_response.json()["id"]

    time.sleep(scheduler_module.SCHEDULER_POLL_INTERVAL_SECONDS + 1.5)

    db_path = tmp_path / "test_printers.db"
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT status, next_heartbeat_at FROM printers WHERE id = ?",
            (printer_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    status, next_heartbeat_at = row
    assert status == "IDLE"
    assert next_heartbeat_at is not None
