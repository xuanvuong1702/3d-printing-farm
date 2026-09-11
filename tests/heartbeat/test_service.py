
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import tools.moonraker_simulator.app as simulator_app
from app.heartbeat.service import (
    DEFAULT_BACKOFF_MULTIPLIER,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    run_heartbeat_cycle,
)

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_TOLERANCE_SECONDS = 5.0

_FAR_FUTURE_ISO = "2099-01-01T00:00:00Z"

def _parse(value: str) -> datetime:
    return datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=timezone.utc)

def _assert_close_to_now_plus(value: str, seconds: float) -> None:
    expected = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    actual = _parse(value)
    delta = abs((actual - expected).total_seconds())
    assert delta <= _TOLERANCE_SECONDS, (
        f"{value!r} lệch {delta:.1f}s so với kỳ vọng "
        f"{expected.isoformat()} (dung sai {_TOLERANCE_SECONDS}s)"
    )

def test_online_printer_updates_status_resets_failures_and_sets_next_heartbeat(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer
):
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port)

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "IDLE"
    assert failures == 0
    _assert_close_to_now_plus(next_heartbeat_at, DEFAULT_HEARTBEAT_INTERVAL_SECONDS)

def test_offline_printer_marks_offline_and_increments_failures_first_time(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer
):
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port)
    handle.stop()

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "OFFLINE"
    assert failures == 1
    expected_interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS * (DEFAULT_BACKOFF_MULTIPLIER**1)
    assert expected_interval == 60.0
    _assert_close_to_now_plus(next_heartbeat_at, expected_interval)

def test_printer_not_due_yet_is_skipped(
    heartbeat_db_path, insert_printer, fetch_printer
):
    printer_id = insert_printer(
        "203.0.113.1",
        7125,
        consecutive_heartbeat_failures=3,
        next_heartbeat_at=_FAR_FUTURE_ISO,
    )

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "UNKNOWN"
    assert failures == 3
    assert next_heartbeat_at == _FAR_FUTURE_ISO

def test_consecutive_failures_apply_exponential_backoff(
    simulator_factory,
    heartbeat_db_path,
    insert_printer,
    fetch_printer,
    set_next_heartbeat_at,
):
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port)
    handle.stop()

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    _, failures_after_first, _ = fetch_printer(printer_id)
    assert failures_after_first == 1

    set_next_heartbeat_at(printer_id, "2000-01-01T00:00:00Z")

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "OFFLINE"
    assert failures == 2
    expected_interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS * (DEFAULT_BACKOFF_MULTIPLIER**2)
    assert expected_interval == 120.0
    _assert_close_to_now_plus(next_heartbeat_at, expected_interval)

def test_mixed_online_and_offline_printers_in_one_cycle(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer
):
    online_handle = simulator_factory(host="127.0.0.1")
    offline_handle = simulator_factory(host="127.0.0.2")
    offline_handle.stop()

    online_id = insert_printer(online_handle.host, online_handle.port)
    offline_id = insert_printer(offline_handle.host, offline_handle.port)

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    online_status, online_failures, online_next = fetch_printer(online_id)
    assert online_status == "IDLE"
    assert online_failures == 0
    _assert_close_to_now_plus(online_next, DEFAULT_HEARTBEAT_INTERVAL_SECONDS)

    offline_status, offline_failures, _ = fetch_printer(offline_id)
    assert offline_status == "OFFLINE"
    assert offline_failures == 1

def test_run_heartbeat_cycle_on_empty_table_does_not_raise(heartbeat_db_path):
    run_heartbeat_cycle(db_path=heartbeat_db_path)

_PAST_ISO = "2000-01-01T00:00:00Z"

def test_is_held_set_when_transition_to_finished_with_active_job(
    simulator_factory,
    heartbeat_db_path,
    insert_printer,
    fetch_printer,
    fetch_is_held,
    set_next_heartbeat_at,
):
    handle = simulator_factory(host="127.0.0.1")
    simulator_app.state.print_stats_state = "printing"
    simulator_app.state.print_stats_filename = "cube.gcode"
    printer_id = insert_printer(handle.host, handle.port)

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    status, _, _ = fetch_printer(printer_id)
    assert status == "PRINTING"
    assert fetch_is_held(printer_id) is False

    simulator_app.state.print_stats_state = "complete"
    set_next_heartbeat_at(printer_id, _PAST_ISO)

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    status, _, _ = fetch_printer(printer_id)
    assert status == "FINISHED"
    assert fetch_is_held(printer_id) is True

def test_is_held_set_when_transition_to_error_with_active_job(
    simulator_factory,
    heartbeat_db_path,
    insert_printer,
    fetch_printer,
    fetch_is_held,
    set_next_heartbeat_at,
):
    handle = simulator_factory(host="127.0.0.1")
    simulator_app.state.print_stats_state = "printing"
    simulator_app.state.print_stats_filename = "cube.gcode"
    printer_id = insert_printer(handle.host, handle.port)

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    assert fetch_is_held(printer_id) is False

    simulator_app.state.print_stats_state = "error"
    set_next_heartbeat_at(printer_id, _PAST_ISO)

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    status, _, _ = fetch_printer(printer_id)
    assert status == "ERROR"
    assert fetch_is_held(printer_id) is True

def test_is_held_not_set_when_finished_without_prior_active_job(
    simulator_factory,
    heartbeat_db_path,
    insert_printer,
    fetch_printer,
    fetch_is_held,
    set_next_heartbeat_at,
):
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port)

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    status, _, _ = fetch_printer(printer_id)
    assert status == "IDLE"
    assert fetch_is_held(printer_id) is False

    simulator_app.state.print_stats_state = "complete"
    set_next_heartbeat_at(printer_id, _PAST_ISO)

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    status, _, _ = fetch_printer(printer_id)
    assert status == "FINISHED"
    assert fetch_is_held(printer_id) is False

def test_is_held_auto_clears_on_recovery_to_printing_with_active_job(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer, fetch_is_held
):
    handle = simulator_factory(host="127.0.0.1")
    simulator_app.state.print_stats_state = "printing"
    simulator_app.state.print_stats_filename = "cube.gcode"
    printer_id = insert_printer(handle.host, handle.port, is_held=1)

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, _, _ = fetch_printer(printer_id)
    assert status == "PRINTING"
    assert fetch_is_held(printer_id) is False

def test_is_held_stays_set_on_recovery_to_printing_without_active_job(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer, fetch_is_held
):
    handle = simulator_factory(host="127.0.0.1")
    simulator_app.state.print_stats_state = "printing"
    printer_id = insert_printer(handle.host, handle.port, is_held=1)

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, _, _ = fetch_printer(printer_id)
    assert status == "PRINTING"
    assert fetch_is_held(printer_id) is True

def test_is_held_unchanged_when_printer_goes_offline(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer, fetch_is_held
):
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port, is_held=1)
    handle.stop()

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, _, _ = fetch_printer(printer_id)
    assert status == "OFFLINE"
    assert fetch_is_held(printer_id) is True
