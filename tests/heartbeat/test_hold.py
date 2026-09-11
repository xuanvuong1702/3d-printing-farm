
from __future__ import annotations

from app.heartbeat.service import compute_updated_is_held

def test_finished_with_active_job_sets_hold():
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="FINISHED",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is True

def test_error_with_active_job_sets_hold():
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="ERROR",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is True

def test_finished_without_active_job_does_not_set_hold():
    result = compute_updated_is_held(
        old_status="IDLE",
        new_status="FINISHED",
        is_held=False,
        filename=None,
    )
    assert result is False

def test_status_outside_trigger_set_does_not_set_hold():
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="PAUSED",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is False

def test_repeated_finished_without_real_transition_does_not_reset_hold_trigger():
    result = compute_updated_is_held(
        old_status="FINISHED",
        new_status="FINISHED",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is False

def test_held_stays_held_when_status_is_offline():
    result = compute_updated_is_held(
        old_status="FINISHED",
        new_status="OFFLINE",
        is_held=True,
        filename="benchy.gcode",
    )
    assert result is True

def test_held_stays_held_when_printing_without_filename():
    result = compute_updated_is_held(
        old_status="ERROR",
        new_status="PRINTING",
        is_held=True,
        filename=None,
    )
    assert result is True

def test_held_auto_clears_on_recovery_to_printing_with_real_job():
    result = compute_updated_is_held(
        old_status="ERROR",
        new_status="PRINTING",
        is_held=True,
        filename="benchy.gcode",
    )
    assert result is False

def test_not_held_and_printing_with_filename_stays_unheld():
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="PRINTING",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is False
