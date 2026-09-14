
from __future__ import annotations

import sqlite3

import tools.moonraker_simulator.app as simulator_app
from app.history.service import run_history_sync_cycle

def test_run_history_sync_cycle_full_flow_against_simulator(
    simulator, history_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    printer_id = insert_printer("127.0.0.1", simulator)
    job_id = insert_job(printer_id, "integration_test.gcode", status="printing")

    simulator_app.state.history_entries.append(
        {
            "job_id": "moonraker-integration-1",
            "filename": "integration_test.gcode",
            "status": "completed",
            "start_time": 1_700_000_000.0,
            "end_time": 1_700_000_600.0,
            "print_duration": 600.0,
            "total_duration": 600.0,
            "filament_used": 12.3,
            "metadata": {},
            "auxiliary_data": [
                {"provider": "spoolman", "name": "spool_ids", "value": [99]}
            ],
        }
    )

    run_history_sync_cycle(db_path=history_db_path)

    assert fetch_job_status(job_id) == "finished"

    connection = sqlite3.connect(history_db_path)
    try:
        row = connection.execute(
            "SELECT filename, status, spool_id, moonraker_job_id "
            "FROM job_history WHERE printer_id = ?",
            (printer_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row == (
        "integration_test.gcode",
        "finished",
        "99",
        "moonraker-integration-1",
    )

def test_run_history_sync_cycle_second_poll_does_not_duplicate(
    simulator, history_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    printer_id = insert_printer("127.0.0.1", simulator)
    job_id = insert_job(printer_id, "second_poll.gcode", status="printing")

    simulator_app.state.history_entries.append(
        {
            "job_id": "moonraker-integration-2",
            "filename": "second_poll.gcode",
            "status": "completed",
            "start_time": 4_102_444_800.0,
            "end_time": 4_102_445_400.0,
            "auxiliary_data": [],
        }
    )

    run_history_sync_cycle(db_path=history_db_path)
    run_history_sync_cycle(db_path=history_db_path)

    assert fetch_job_status(job_id) == "finished"

    connection = sqlite3.connect(history_db_path)
    try:
        (count,) = connection.execute(
            "SELECT COUNT(*) FROM job_history WHERE printer_id = ?",
            (printer_id,),
        ).fetchone()
    finally:
        connection.close()

    assert count == 1
