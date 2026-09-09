"""
Unit test cho `app/db/migrate.py` + `app/db/schema.py` (chunk C4, E0-4).

Chuyển các case đã kiểm chứng thủ công ở chunk C2/C3 (xem `Story_E0-4.md`
mục "Cách chạy/kiểm chứng") thành assertion tự động - không thêm case
mới ngoài phạm vi đã kiểm chứng, đúng phạm vi C4 đã khai báo trong
`State_E0-4_v5.md`.

Dùng file tạm (`tmp_path`, fixture chuẩn của pytest) thay vì `:memory:`
cho các test cần đọc lại dữ liệu bằng 1 kết nối riêng sau khi
`run_migrations` đã đóng kết nối của nó - `:memory:` là DB riêng theo
từng kết nối `sqlite3.connect`, không dùng chung được giữa 2 lần connect
khác nhau. Test tách biệt "chạy trên `:memory:` không lỗi cú pháp" khỏi
"đọc lại dữ liệu sau khi migrate" theo đúng 2 mục đích khác nhau.

Mỗi kết nối mở riêng trong test phải tự bật lại
`PRAGMA foreign_keys = ON` - đây là thiết lập theo từng kết nối của
sqlite3, không phải thiết lập của riêng file DB (kết nối mà
`run_migrations` dùng để tạo bảng đã đóng lại sau khi migrate xong).
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db.migrate import run_migrations
from app.db.schema import (
    CREATE_EVENTS_SQL,
    CREATE_JOB_HISTORY_SQL,
    CREATE_JOBS_SQL,
    CREATE_PRINTERS_SQL,
    CREATE_USERS_SQL,
)

ALL_TABLES = ["printers", "jobs", "job_history", "events", "users"]

@pytest.fixture
def migrated_connection(tmp_path):
    """DB file tạm đã chạy `run_migrations`, mở lại 1 kết nối riêng để
    test insert/query, có bật `PRAGMA foreign_keys = ON` (bắt buộc lại
    theo từng kết nối)."""
    db_path = str(tmp_path / "test_print_farm.db")
    run_migrations(db_path)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()

def test_run_migrations_on_memory_db_no_syntax_error():
    """Chạy trên `:memory:` không raise lỗi cú pháp SQL (mọi CREATE_*_SQL)."""
    run_migrations(":memory:")

def test_run_migrations_creates_all_five_tables(migrated_connection):
    tables = {
        row[0]
        for row in migrated_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table in ALL_TABLES:
        assert table in tables

def test_run_migrations_is_idempotent(tmp_path):
    """Gọi `run_migrations` 2 lần trên cùng 1 file không raise lỗi."""
    db_path = str(tmp_path / "idempotent.db")
    run_migrations(db_path)
    run_migrations(db_path)

def test_printers_default_values(migrated_connection):
    migrated_connection.execute(
        "INSERT INTO printers (name, ip) VALUES ('QIDI-01', '192.168.1.50')"
    )
    row = migrated_connection.execute(
        "SELECT moonraker_port, capabilities, status, is_held"
        " FROM printers WHERE ip = '192.168.1.50'"
    ).fetchone()
    assert row == (7125, "{}", "UNKNOWN", 0)

def test_jobs_default_values(migrated_connection):
    printer_id = _insert_printer(migrated_connection)
    migrated_connection.execute(
        "INSERT INTO jobs (printer_id, filename) VALUES (?, 'a.gcode')",
        (printer_id,),
    )
    row = migrated_connection.execute(
        "SELECT status, priority FROM jobs WHERE printer_id = ?", (printer_id,)
    ).fetchone()
    assert row == ("queued", 0)

def test_users_default_role_is_viewer(migrated_connection):
    migrated_connection.execute(
        "INSERT INTO users (username, password_hash) VALUES ('admin', 'hash')"
    )
    row = migrated_connection.execute(
        "SELECT role FROM users WHERE username = 'admin'"
    ).fetchone()
    assert row == ("viewer",)

def test_printers_status_check_rejects_invalid_value(migrated_connection):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO printers (name, ip, status) VALUES ('X', '1.1.1.1', 'BAD')"
        )

def test_jobs_status_check_rejects_invalid_value(migrated_connection):
    printer_id = _insert_printer(migrated_connection)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO jobs (printer_id, filename, status) VALUES (?, 'a', 'bad')",
            (printer_id,),
        )

def test_job_history_status_check_rejects_value_outside_terminal_subset(
    migrated_connection,
):
    """job_history.status chỉ nhận tập con 'finished'/'failed'/'cancelled'
    của jobs.status (D-013) - 'printing' hợp lệ cho jobs nhưng KHÔNG hợp
    lệ cho job_history."""
    printer_id = _insert_printer(migrated_connection)
    job_id = _insert_job(migrated_connection, printer_id)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO job_history (job_id, printer_id, filename, status)"
            " VALUES (?, ?, 'a', 'printing')",
            (job_id, printer_id),
        )

def test_users_role_check_rejects_invalid_value(migrated_connection):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO users (username, password_hash, role)"
            " VALUES ('x', 'h', 'superadmin')"
        )

def test_printers_ip_unique_rejects_duplicate(migrated_connection):
    _insert_printer(migrated_connection, ip="10.0.0.1")
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO printers (name, ip) VALUES ('Y', '10.0.0.1')"
        )

def test_users_username_unique_rejects_duplicate(migrated_connection):
    migrated_connection.execute(
        "INSERT INTO users (username, password_hash) VALUES ('admin', 'h1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO users (username, password_hash) VALUES ('admin', 'h2')"
        )

def test_jobs_printer_id_foreign_key_rejects_nonexistent_printer(
    migrated_connection,
):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO jobs (printer_id, filename) VALUES (9999, 'a.gcode')"
        )

def test_valid_dependency_chain_printer_job_job_history_event(migrated_connection):
    printer_id = _insert_printer(migrated_connection)
    job_id = _insert_job(migrated_connection, printer_id)
    migrated_connection.execute(
        "INSERT INTO job_history (job_id, printer_id, filename, status)"
        " VALUES (?, ?, 'a.gcode', 'finished')",
        (job_id, printer_id),
    )
    migrated_connection.execute(
        "INSERT INTO events (printer_id, job_id, event_type, message)"
        " VALUES (?, ?, 'job_finished', 'ok')",
        (printer_id, job_id),
    )
    count = migrated_connection.execute(
        "SELECT COUNT(*) FROM job_history WHERE job_id = ?", (job_id,)
    ).fetchone()[0]
    assert count == 1

def test_event_without_printer_or_job_is_allowed(migrated_connection):
    """`events.printer_id`/`job_id` đều nullable - event hệ thống không
    gắn 1 máy/job cụ thể vẫn insert được (Story_E0-4.md mục "4. events")."""
    migrated_connection.execute(
        "INSERT INTO events (event_type) VALUES ('system_startup')"
    )
    row = migrated_connection.execute(
        "SELECT printer_id, job_id FROM events WHERE event_type = 'system_startup'"
    ).fetchone()
    assert row == (None, None)

def _insert_printer(connection: sqlite3.Connection, ip: str = "192.168.1.99") -> int:
    connection.execute(
        "INSERT INTO printers (name, ip) VALUES ('QIDI-test', ?)", (ip,)
    )
    return connection.execute(
        "SELECT id FROM printers WHERE ip = ?", (ip,)
    ).fetchone()[0]

def _insert_job(connection: sqlite3.Connection, printer_id: int) -> int:
    connection.execute(
        "INSERT INTO jobs (printer_id, filename) VALUES (?, 'a.gcode')",
        (printer_id,),
    )
    return connection.execute(
        "SELECT id FROM jobs WHERE printer_id = ?", (printer_id,)
    ).fetchone()[0]

assert all(
    isinstance(sql, str)
    for sql in (
        CREATE_PRINTERS_SQL,
        CREATE_JOBS_SQL,
        CREATE_JOB_HISTORY_SQL,
        CREATE_EVENTS_SQL,
        CREATE_USERS_SQL,
    )
)
