"""
Chạy migration schema SQLite bằng `sqlite3` thuần (không ORM - xem
CLAUDE.md mục "Quy ước code").

Phạm vi chunk E0-4/C2: chạy `CREATE_PRINTERS_SQL`.
Phạm vi chunk E0-4/C3 (chunk này): thêm 4 bảng còn lại - `jobs`,
`job_history`, `events`, `users` - vào cùng hàm `run_migrations`, chạy
tuần tự đúng thứ tự phụ thuộc FK: `printers` -> `jobs` (FK printer_id) ->
`job_history` (FK job_id, printer_id) -> `events` (FK printer_id, job_id,
đều nullable) -> `users` (không FK, độc lập).

Đường dẫn DB mặc định là `print_farm.db` (tương đối, khớp
`WorkingDirectory=/opt/qidi-farm` khi chạy qua systemd - xem
`deploy/qidi-farm.service`). Không hard-code đường dẫn tuyệt đối.

Chạy độc lập: `python -m app.db.migrate`.
Chạy từ code khác (ví dụ test): `run_migrations(":memory:")` hoặc một
đường dẫn file tạm.
"""

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
    """Mở kết nối sqlite3 tới `db_path` và chạy tuần tự các CREATE TABLE.

    Bật `PRAGMA foreign_keys = ON` trước khi tạo bảng vì các bảng
    `jobs`/`job_history`/`events` có `REFERENCES` trỏ tới
    `printers`/`jobs` - cần FK constraint có hiệu lực ngay từ đầu, không
    phải bật thêm sau khi đã có dữ liệu. Thứ tự chạy đúng theo phụ thuộc
    FK: `printers` trước `jobs`; `jobs` + `printers` trước `job_history`
    và `events`; `users` không FK nên chạy sau cùng cho gọn (không bắt
    buộc về thứ tự).
    """
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
