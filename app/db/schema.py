"""
Định nghĩa schema SQLite bằng các chuỗi `CREATE TABLE IF NOT EXISTS ...`
(idempotent - chạy lại nhiều lần không lỗi, xem CLAUDE.md mục "Quy ước
code" / "Migration").

Phạm vi chunk E0-4/C2: chỉ bảng `printers` (`CREATE_PRINTERS_SQL`). 4 bảng
còn lại (`jobs`, `job_history`, `events`, `users`) thuộc chunk E0-4/C3,
theo nguyên tắc kích thước chunk (mục 1 Loop-Controller) - tránh gộp cả 5
bảng vào 1 chunk quá dài.

Nguồn thiết kế cột đầy đủ: `docs/Story_E0-4.md` mục "1. `printers`".
Quyết định áp dụng:
- D-001: `moonraker_port` mặc định 7125.
- D-003: `api_key` là cột nullable.
- D-007/D-012: `capabilities` (JSON dạng TEXT) + `model` phục vụ
  capability detection / resolution driver.
- D-010: `is_held` - cơ chế xác nhận vận hành viên, bắt buộc có ở bảng
  `printers` dù AC gốc của E0-4 không liệt kê tường minh (đã xác nhận ở
  chunk C1, xem `Story_E0-4.md`).
- D-013: `CHECK` cho `status` theo canonical set đầy đủ 8 giá trị.

Quy ước code (CLAUDE.md, điền lần đầu ở E0-4/C1):
- boolean lưu `INTEGER` với `CHECK (col IN (0,1))` (`is_held`).
- timestamp lưu `TEXT` ISO-8601 UTC, default tính ở tầng SQL bằng
  `strftime('%Y-%m-%dT%H:%M:%SZ','now')`.
- mọi cột canonical status có `CHECK` tường minh tại DDL, không chỉ
  validate ở tầng ứng dụng.
"""

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
