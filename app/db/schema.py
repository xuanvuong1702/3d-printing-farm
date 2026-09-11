"""
Định nghĩa schema SQLite bằng các chuỗi `CREATE TABLE IF NOT EXISTS ...`
(idempotent - chạy lại nhiều lần không lỗi, xem CLAUDE.md mục "Quy ước
code" / "Migration").

Phạm vi chunk E0-4/C2: bảng `printers` (`CREATE_PRINTERS_SQL`).
Phạm vi chunk E0-4/C3 (chunk này): 4 bảng còn lại - `jobs`, `job_history`,
`events`, `users` - hoàn tất đủ 5 bảng ERD.

Nguồn thiết kế cột đầy đủ: `docs/Story_E0-4.md` mục "1. `printers`" tới
"5. `users`".
Quyết định áp dụng:
- D-001: `moonraker_port` mặc định 7125.
- D-003: `api_key` là cột nullable.
- D-007/D-012: `capabilities` (JSON dạng TEXT) + `model` phục vụ
  capability detection / resolution driver.
- D-010: `is_held` - cơ chế xác nhận vận hành viên, bắt buộc có ở bảng
  `printers` dù AC gốc của E0-4 không liệt kê tường minh (đã xác nhận ở
  chunk C1, xem `Story_E0-4.md`).
- D-011: không tạo cột riêng cho reservation lock ở E0-4 - chỉ ảnh hưởng
  tầng dispatch (story sau), không có mặt trong schema chunk này.
- D-013: `CHECK` cho `printers.status` (8 giá trị), `jobs.status` (6 giá
  trị), và `job_history.status` (3 giá trị - tập con của `jobs.status`,
  chỉ ghi khi job đã kết thúc).

Phạm vi E1-4/C1 (chunk này): thêm 2 cột mới vào `CREATE_PRINTERS_SQL`
phục vụ heartbeat scheduler nền (xem `docs/State_E1-4_v2.md` mục "Quyết
định phạm vi chốt tại chunk C0", Quyết định 4 - không phải `D-00X` mới,
là thiết kế schema cục bộ của story E1-4):
- `consecutive_heartbeat_failures`: đếm số lần heartbeat lỗi liên tiếp
  cho 1 máy, dùng để tính backoff tăng dần (xem
  `app/heartbeat/service.py`). Không có `CHECK` - không phải enum/
  canonical status, chỉ là bộ đếm.
- `next_heartbeat_at`: thời điểm (ISO-8601 UTC, cùng định dạng
  `created_at`/`updated_at`) tới lượt heartbeat kế tiếp cho máy đó.
  Nullable - `NULL` nghĩa là "chưa từng heartbeat, đến lượt ngay".
  Không có default ở tầng SQL (khác `created_at`/`updated_at`) vì giá
  trị `NULL` mặc định đã đúng ý nghĩa "đến lượt ngay", không cần tính
  bằng `strftime`.

Quy ước code (CLAUDE.md, điền lần đầu ở E0-4/C1):
- boolean lưu `INTEGER` với `CHECK (col IN (0,1))` (`is_held`).
- timestamp lưu `TEXT` ISO-8601 UTC, default tính ở tầng SQL bằng
  `strftime('%Y-%m-%dT%H:%M:%SZ','now')`.
- mọi cột canonical status có `CHECK` tường minh tại DDL, không chỉ
  validate ở tầng ứng dụng.

Phạm vi E3-3/C1 (chunk này): thêm cột `power_device_name` vào
`CREATE_PRINTERS_SQL` (xem `docs/State_E3-3_v2.md` mục "Quyết định
phạm vi" điểm 3 - không phải `D-00X` mới, là thiết kế schema cục bộ
của story E3-3):
- `power_device_name`: tên device Machine/Power API (Moonraker) gắn
  với máy in này (ví dụ tên `[power <device_name>]` cấu hình trong
  `moonraker.conf` của máy đó) - dùng khi gọi
  `app/moonraker/http_client.py::set_device_power`. `TEXT`, nullable,
  KHÔNG có `CHECK` (không phải enum/canonical status). `NULL` nghĩa là
  "máy có thể có capability `power` (cột `capabilities`) nhưng CHƯA
  được cấu hình tên device qua `PATCH /printers/{id}`" - khác hẳn máy
  hoàn toàn không có capability `power`. Cấu hình qua
  `PrinterUpdateRequest` mở rộng (E1-3 đã có cơ chế
  `model_dump(exclude_unset=True)`), không phải capability detection
  tự động (khác `moonraker_version`/`klipper_version`/`capabilities`).

Ghi chú riêng cho chunk C3 (xem `Story_E0-4.md` mục "Quyết định MỚI phát
sinh..." cho lý do đầy đủ - đều là thiết kế schema cụ thể, không phải
`D-00X` mới):
- `jobs.priority`: chỗ lưu trữ cho E4-4 (reorder hàng đợi), chưa có thuật
  toán cụ thể.
- `job_history.printer_id`: trùng lặp có chủ đích với `jobs.printer_id`
  (qua JOIN) để báo cáo theo máy (E5-2) không phụ thuộc bản ghi `jobs`
  gốc còn tồn tại.
- `events`: thiết kế tối thiểu suy luận từ AC E2-3/E9-1/E8-4, `printer_id`
  và `job_id` đều NULL-able (event có thể không gắn máy/job cụ thể).
  `event_type` KHÔNG có `CHECK` - chưa có canonical set nào chốt cho các
  loại event (khác `status`, đã có D-013).
- `users.password_hash`: lưu hash, không lưu plaintext; thuật toán hash
  cụ thể chưa chốt (để E7-2 quyết định).
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
    power_device_name TEXT,
    status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (status IN (
            'IDLE', 'PRINTING', 'PAUSED', 'FINISHED',
            'STOPPED', 'ERROR', 'OFFLINE', 'UNKNOWN'
        )),
    is_held INTEGER NOT NULL DEFAULT 0
        CHECK (is_held IN (0, 1)),
    consecutive_heartbeat_failures INTEGER NOT NULL DEFAULT 0,
    next_heartbeat_at TEXT,
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
