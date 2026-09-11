
from __future__ import annotations

import sqlite3

from app.db.migrate import DEFAULT_DB_PATH
from app.drivers import resolve_driver
from app.moonraker.http_client import ACTIVE_JOB_STATUSES, MoonrakerClientError

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0

DEFAULT_BACKOFF_MULTIPLIER = 2.0

DEFAULT_BACKOFF_MAX_SECONDS = 300.0

_OFFLINE_STATUS = "OFFLINE"

_HOLD_TRIGGER_STATUSES = frozenset({"FINISHED", "ERROR"})

_HOLD_AUTO_CLEAR_STATUS = "PRINTING"

_SELECT_DUE_PRINTERS_SQL = """
SELECT id, ip, moonraker_port, model, api_key, klipper_version,
       consecutive_heartbeat_failures, status, is_held
FROM printers
WHERE next_heartbeat_at IS NULL OR next_heartbeat_at <= ?
"""

_UPDATE_HEARTBEAT_RESULT_SQL = """
UPDATE printers
SET status = ?,
    consecutive_heartbeat_failures = ?,
    next_heartbeat_at = ?,
    is_held = ?,
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

_NOW_SQL = "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

_NEXT_HEARTBEAT_AT_SQL = (
    "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+' || ? || ' seconds')"
)

def _compute_backoff_interval_seconds(consecutive_failures: int) -> float:
    interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS * (
        DEFAULT_BACKOFF_MULTIPLIER**consecutive_failures
    )
    return min(interval, DEFAULT_BACKOFF_MAX_SECONDS)

def compute_updated_is_held(
    *,
    old_status: str,
    new_status: str,
    is_held: bool,
    filename: "str | None",
) -> bool:
    if is_held:
        auto_recovered = (
            new_status == _HOLD_AUTO_CLEAR_STATUS and filename is not None
        )
        return not auto_recovered

    is_real_transition = new_status != old_status
    reached_hold_status = new_status in _HOLD_TRIGGER_STATUSES
    has_active_job = old_status in ACTIVE_JOB_STATUSES
    return is_real_transition and reached_hold_status and has_active_job

def run_heartbeat_cycle(db_path: str = DEFAULT_DB_PATH) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        (now,) = connection.execute(_NOW_SQL).fetchone()

        due_rows = connection.execute(_SELECT_DUE_PRINTERS_SQL, (now,)).fetchall()

        for (
            printer_id,
            ip,
            moonraker_port,
            model,
            api_key,
            klipper_version,
            consecutive_failures,
            old_status,
            is_held,
        ) in due_rows:
            driver = resolve_driver(
                model=model,
                firmware_version=klipper_version,
                host=ip,
                port=moonraker_port,
                api_key=api_key,
            )

            try:
                printer_status = driver.get_status()
                canonical_status = printer_status.canonical_status
            except MoonrakerClientError:

                printer_status = None
                canonical_status = None

            if canonical_status is None:
                new_status = _OFFLINE_STATUS
                new_consecutive_failures = consecutive_failures + 1
                interval_seconds = _compute_backoff_interval_seconds(
                    new_consecutive_failures
                )

                new_is_held = bool(is_held)
            else:
                new_status = canonical_status
                new_consecutive_failures = 0
                interval_seconds = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
                new_is_held = compute_updated_is_held(
                    old_status=old_status,
                    new_status=canonical_status,
                    is_held=bool(is_held),
                    filename=printer_status.filename,
                )

            (next_heartbeat_at,) = connection.execute(
                _NEXT_HEARTBEAT_AT_SQL, (interval_seconds,)
            ).fetchone()

            connection.execute(
                _UPDATE_HEARTBEAT_RESULT_SQL,
                (
                    new_status,
                    new_consecutive_failures,
                    next_heartbeat_at,
                    int(new_is_held),
                    printer_id,
                ),
            )
            connection.commit()
    finally:
        connection.close()
