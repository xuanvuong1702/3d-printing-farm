"""
Chạy migration schema SQLite bằng `sqlite3` thuần (không ORM - xem
CLAUDE.md mục "Quy ước code").

Phạm vi chunk E0-4/C2: mới chạy `CREATE_PRINTERS_SQL`. Chunk E0-4/C3 sẽ
thêm 4 bảng còn lại (`jobs`, `job_history`, `events`, `users`) vào cùng
hàm `run_migrations`, chạy tuần tự theo đúng thứ tự (bảng nào có FK trỏ
tới bảng khác phải chạy sau bảng được trỏ tới).

Đường dẫn DB mặc định là `print_farm.db` (tương đối, khớp
`WorkingDirectory=/opt/qidi-farm` khi chạy qua systemd - xem
`deploy/qidi-farm.service`). Không hard-code đường dẫn tuyệt đối.

Chạy độc lập: `python -m app.db.migrate`.
Chạy từ code khác (ví dụ test): `run_migrations(":memory:")` hoặc một
đường dẫn file tạm.
"""

from __future__ import annotations

import sqlite3

from app.db.schema import CREATE_PRINTERS_SQL

DEFAULT_DB_PATH = "print_farm.db"

def run_migrations(db_path: str = DEFAULT_DB_PATH) -> None:
    """Mở kết nối sqlite3 tới `db_path` và chạy tuần tự các CREATE TABLE.

    Bật `PRAGMA foreign_keys = ON` trước khi tạo bảng vì các bảng thêm ở
    E0-4/C3 (`jobs`, `job_history`, `events`) có `REFERENCES` trỏ tới
    `printers`/`jobs` - cần FK constraint có hiệu lực ngay từ đầu, không
    phải bật thêm sau khi đã có dữ liệu.
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(CREATE_PRINTERS_SQL)
        connection.commit()
    finally:
        connection.close()

if __name__ == "__main__":
    run_migrations()
