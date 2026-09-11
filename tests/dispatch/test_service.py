
from __future__ import annotations

import threading

import app.moonraker.http_client as http_client_module
from app.dispatch.service import run_dispatch_cycle
from app.moonraker.http_client import upload_file

_GCODE_CONTENT = b"G28\nG1 X10 Y10\n"

def _seed_uploaded_file(host: str, port: int, filename: str) -> None:
    upload_file(host, filename, _GCODE_CONTENT, port=port)

def test_no_idle_printers_is_noop(dispatch_db_path) -> None:
    run_dispatch_cycle(db_path=dispatch_db_path)

def test_held_printer_is_skipped(
    dispatch_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    printer_id = insert_printer("10.0.0.1", 7125, status="IDLE", is_held=1)
    job_id = insert_job(printer_id, "held.gcode")

    run_dispatch_cycle(db_path=dispatch_db_path)

    assert fetch_job_status(job_id) == "queued"

def test_job_queue_capable_printer_is_skipped(
    dispatch_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    printer_id = insert_printer(
        "10.0.0.2", 7125, status="IDLE", is_held=0, capabilities=["job_queue"]
    )
    job_id = insert_job(printer_id, "queue_capable.gcode")

    run_dispatch_cycle(db_path=dispatch_db_path)

    assert fetch_job_status(job_id) == "queued"

def test_candidate_printer_without_queued_job_is_noop(
    dispatch_db_path, insert_printer
) -> None:
    insert_printer("10.0.0.3", 7125, status="IDLE", is_held=0)

    run_dispatch_cycle(db_path=dispatch_db_path)

def test_happy_path_claims_job_and_starts_print(
    simulator, dispatch_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    printer_id = insert_printer("127.0.0.1", simulator, status="IDLE", is_held=0)
    job_id = insert_job(printer_id, "happy_path.gcode")
    _seed_uploaded_file("127.0.0.1", simulator, "happy_path.gcode")

    run_dispatch_cycle(db_path=dispatch_db_path)

    assert fetch_job_status(job_id) == "printing"

def test_selects_next_job_by_priority_then_fifo(
    simulator, dispatch_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    printer_id = insert_printer("127.0.0.1", simulator, status="IDLE", is_held=0)
    low_priority_first = insert_job(
        printer_id, "low_first.gcode", priority=0, created_at="2026-01-01T00:00:01Z"
    )
    high_priority_later = insert_job(
        printer_id, "high_later.gcode", priority=5, created_at="2026-01-01T00:00:02Z"
    )
    low_priority_last = insert_job(
        printer_id, "low_last.gcode", priority=0, created_at="2026-01-01T00:00:03Z"
    )
    for filename in ("low_first.gcode", "high_later.gcode", "low_last.gcode"):
        _seed_uploaded_file("127.0.0.1", simulator, filename)

    run_dispatch_cycle(db_path=dispatch_db_path)

    assert fetch_job_status(high_priority_later) == "printing"
    assert fetch_job_status(low_priority_first) == "queued"
    assert fetch_job_status(low_priority_last) == "queued"

def test_moonraker_error_marks_job_failed_without_raising(
    simulator_factory, dispatch_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port, status="IDLE", is_held=0)
    job_id = insert_job(printer_id, "will_fail.gcode")
    handle.stop()

    run_dispatch_cycle(db_path=dispatch_db_path)

    assert fetch_job_status(job_id) == "failed"

def test_sequential_second_call_is_noop_after_job_already_claimed(
    simulator, dispatch_db_path, insert_printer, insert_job, fetch_job_status
) -> None:
    printer_id = insert_printer("127.0.0.1", simulator, status="IDLE", is_held=0)
    job_id = insert_job(printer_id, "sequential.gcode")
    _seed_uploaded_file("127.0.0.1", simulator, "sequential.gcode")

    run_dispatch_cycle(db_path=dispatch_db_path)
    assert fetch_job_status(job_id) == "printing"

    run_dispatch_cycle(db_path=dispatch_db_path)
    assert fetch_job_status(job_id) == "printing"

def test_reservation_lock_prevents_double_dispatch_on_concurrent_calls(
    simulator, dispatch_db_path, insert_printer, insert_job, fetch_job_status, monkeypatch
) -> None:
    printer_id = insert_printer("127.0.0.1", simulator, status="IDLE", is_held=0)
    job_id = insert_job(printer_id, "race.gcode")
    _seed_uploaded_file("127.0.0.1", simulator, "race.gcode")

    call_count_lock = threading.Lock()
    call_count = {"n": 0}
    real_start_uploaded_print = http_client_module.start_uploaded_print

    def _counting_start_uploaded_print(*args, **kwargs):
        with call_count_lock:
            call_count["n"] += 1
        return real_start_uploaded_print(*args, **kwargs)

    monkeypatch.setattr(
        http_client_module, "start_uploaded_print", _counting_start_uploaded_print
    )

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _run_one_tick() -> None:
        barrier.wait()
        try:
            run_dispatch_cycle(db_path=dispatch_db_path)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_run_one_tick) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not errors, f"run_dispatch_cycle raise trong tick chồng lấn: {errors}"
    assert call_count["n"] == 1
    assert fetch_job_status(job_id) == "printing"
