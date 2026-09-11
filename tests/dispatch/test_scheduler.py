
from __future__ import annotations

import asyncio
import warnings

import app.dispatch.scheduler as scheduler_module

def test_start_and_stop_scheduler_cancels_cleanly_without_warnings(monkeypatch):
    call_count = 0

    def _stub_run_dispatch_cycle() -> None:
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(scheduler_module, "run_dispatch_cycle", _stub_run_dispatch_cycle)

    async def _scenario() -> "asyncio.Task[None]":
        task = scheduler_module.start_dispatch_scheduler(poll_interval_seconds=0.05)

        await asyncio.sleep(0.3)
        await scheduler_module.stop_dispatch_scheduler(task)
        return task

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        task = asyncio.run(_scenario())

    assert task.cancelled()
    assert call_count >= 1

def test_lifespan_wires_real_background_scheduler_dispatches_queued_job(
    client, simulator_factory, tmp_path
) -> None:
    import json
    import sqlite3
    import time

    from app.moonraker.http_client import upload_file

    handle = simulator_factory(host="127.0.0.1")

    db_path = str(tmp_path / "test_printers.db")

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO printers (name, ip, moonraker_port, capabilities, "
            "status, is_held) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "Scheduler Wiring Printer",
                handle.host,
                handle.port,
                json.dumps(["klippy_connection", "file_manager"]),
                "IDLE",
                0,
            ),
        )
        printer_id = connection.execute(
            "SELECT id FROM printers WHERE ip = ?", (handle.host,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO jobs (printer_id, filename, status) VALUES (?, ?, ?)",
            (printer_id, "scheduler_wiring.gcode", "queued"),
        )
        connection.commit()
        job_id = connection.execute(
            "SELECT id FROM jobs WHERE printer_id = ?", (printer_id,)
        ).fetchone()[0]
    finally:
        connection.close()

    upload_file(handle.host, "scheduler_wiring.gcode", b"G28\n", port=handle.port)

    time.sleep(scheduler_module.SCHEDULER_POLL_INTERVAL_SECONDS + 1.5)

    connection = sqlite3.connect(db_path)
    try:
        (status,) = connection.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    finally:
        connection.close()

    assert status == "printing"
