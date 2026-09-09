
from __future__ import annotations

CREATE_PRINTERS_SQL = """
CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ip TEXT NOT NULL UNIQUE,
    moonraker_port INTEGER NOT NULL DEFAULT 7125,
    model TEXT,
    api_key TEXT,
    moonraker_version TEXT,
    klipper_version TEXT,
    capabilities TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (status IN (
            'IDLE', 'PRINTING', 'PAUSED', 'FINISHED',
            'STOPPED', 'ERROR', 'OFFLINE', 'UNKNOWN'
        )),
    is_held INTEGER NOT NULL DEFAULT 0
        CHECK (is_held IN (0, 1)),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""
