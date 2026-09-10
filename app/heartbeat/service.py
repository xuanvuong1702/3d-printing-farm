
from __future__ import annotations

import sqlite3

from app.db.migrate import DEFAULT_DB_PATH
from app.drivers import resolve_driver
from app.moonraker.http_client import MoonrakerClientError

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0

DEFAULT_BACKOFF_MULTIPLIER = 2.0

DEFAULT_BACKOFF_MAX_SECONDS = 300.0

_OFFLINE_STATUS = "OFFLINE"

_SELECT_DUE_PRINTERS_SQL = """
SELECT id, ip, moonraker_port, model, api_key, klipper_version,
       consecutive_heartbeat_failures
FROM printers
WHERE next_heartbeat_at IS NULL OR next_heartbeat_at <= ?
"""

_UPDATE_HEARTBEAT_RESULT_SQL = """
UPDATE printers
SET status = ?,
    consecutive_heartbeat_failures = ?,
    next_heartbeat_at = ?,
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
        ) in due_rows:
            driver = resolve_driver(
                model=model,
                firmware_version=klipper_version,
                host=ip,
                port=moonraker_port,
                api_key=api_key,
            )

            try:
                canonical_status = driver.get_status().canonical_status
            except MoonrakerClientError:

                canonical_status = None

            if canonical_status is None:
                new_status = _OFFLINE_STATUS
                new_consecutive_failures = consecutive_failures + 1
                interval_seconds = _compute_backoff_interval_seconds(
                    new_consecutive_failures
                )
            else:
                new_status = canonical_status
                new_consecutive_failures = 0
                interval_seconds = DEFAULT_HEARTBEAT_INTERVAL_SECONDS

            (next_heartbeat_at,) = connection.execute(
                _NEXT_HEARTBEAT_AT_SQL, (interval_seconds,)
            ).fetchone()

            connection.execute(
                _UPDATE_HEARTBEAT_RESULT_SQL,
                (
                    new_status,
                    new_consecutive_failures,
                    next_heartbeat_at,
                    printer_id,
                ),
            )
            connection.commit()
    finally:
        connection.close()
