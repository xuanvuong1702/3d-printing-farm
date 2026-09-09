
from __future__ import annotations

import sqlite3

from app.db.schema import (
    CREATE_EVENTS_SQL,
    CREATE_JOB_HISTORY_SQL,
    CREATE_JOBS_SQL,
    CREATE_PRINTERS_SQL,
    CREATE_USERS_SQL,
)

DEFAULT_DB_PATH = "print_farm.db"

def run_migrations(db_path: str = DEFAULT_DB_PATH) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(CREATE_PRINTERS_SQL)
        connection.execute(CREATE_JOBS_SQL)
        connection.execute(CREATE_JOB_HISTORY_SQL)
        connection.execute(CREATE_EVENTS_SQL)
        connection.execute(CREATE_USERS_SQL)
        connection.commit()
    finally:
        connection.close()

if __name__ == "__main__":
    run_migrations()
