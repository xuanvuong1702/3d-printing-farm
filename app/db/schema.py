
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

CREATE_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printer_id INTEGER NOT NULL REFERENCES printers(id),
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'queued', 'uploading', 'printing',
            'finished', 'failed', 'cancelled'
        )),
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

CREATE_JOB_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS job_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    printer_id INTEGER NOT NULL REFERENCES printers(id),
    filename TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('finished', 'failed', 'cancelled')),
    start_time TEXT,
    end_time TEXT,
    spool_id TEXT,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printer_id INTEGER REFERENCES printers(id),
    job_id INTEGER REFERENCES jobs(id),
    event_type TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (role IN ('admin', 'viewer')),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""
