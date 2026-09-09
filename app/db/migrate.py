
from __future__ import annotations

import sqlite3

from app.db.schema import CREATE_PRINTERS_SQL

DEFAULT_DB_PATH = "print_farm.db"

def run_migrations(db_path: str = DEFAULT_DB_PATH) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(CREATE_PRINTERS_SQL)
        connection.commit()
    finally:
        connection.close()

if __name__ == "__main__":
    run_migrations()
