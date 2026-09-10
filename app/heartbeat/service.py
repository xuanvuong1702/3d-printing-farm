"""
Logic heartbeat/reconnect cho `app.heartbeat` (E1-4).

Quyết định áp dụng (xem `docs/State_E1-4_v2.md` mục "Quyết định phạm vi
chốt tại chunk C0" cho lý do đầy đủ - đều là thiết kế cục bộ của story
này, không phải `D-00X` mới):
- Quyết định 1: dùng lại kênh HTTP đã có (D-002 phần 1,
  `driver.get_status()` qua `resolve_driver`) - "reconnect" trong AC gốc
  được diễn giải lại thành "thử lại kết nối HTTP", không phải reconnect
  của 1 kết nối WebSocket bền (kênh WS thật thuộc phạm vi E2-1).
- Quyết định 3: tiến trình nền dùng `asyncio.create_task` + FastAPI
  `lifespan`, KHÔNG thêm thư viện scheduler ngoài (vd. APScheduler).
- Quyết định 4: trạng thái backoff lưu ở 2 cột mới trong bảng `printers`
  (`consecutive_heartbeat_failures`, `next_heartbeat_at` -
  `app/db/schema.py`), không giữ in-memory (service có thể restart).
- Quyết định 5: "timeout cấu hình được" = khoảng thời gian giữa 2 lần
  heartbeat check liên tiếp cho 1 máy đang khoẻ mạnh (interval mặc định,
  tham số hoá được), KHÔNG phải timeout kết nối HTTP từng lệnh gọi
  (`INFO_TIMEOUT_SECONDS`, `app/moonraker/http_client.py`, đã khoá từ
  E0-3, không đổi ở đây).

Phạm vi chunk E1-4/C1 (chunk này): CHỈ định nghĩa hằng số cấu hình +
công thức backoff dự kiến trong docstring. Hàm chạy 1 vòng heartbeat
thật (thuần, testable độc lập với async loop) thuộc chunk C2. Vòng lặp
`asyncio` gọi lại hàm đó định kỳ + wiring vào FastAPI `lifespan` thuộc
chunk C3 (`app/heartbeat/scheduler.py`).

Công thức backoff dự kiến dùng ở C2 (cấp số nhân theo
`consecutive_heartbeat_failures`, chặn trần ở
`DEFAULT_BACKOFF_MAX_SECONDS` - Quyết định 5):

    interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS \
        * (DEFAULT_BACKOFF_MULTIPLIER ** consecutive_heartbeat_failures)
    interval = min(interval, DEFAULT_BACKOFF_MAX_SECONDS)
    next_heartbeat_at = now + interval

Heartbeat thành công -> reset `consecutive_heartbeat_failures` về 0,
`next_heartbeat_at` = now + `DEFAULT_HEARTBEAT_INTERVAL_SECONDS` (không
áp backoff). Heartbeat thất bại -> tăng `consecutive_heartbeat_failures`
thêm 1 rồi tính `next_heartbeat_at` theo công thức trên.

Phạm vi chunk E1-4/C2 (chunk này): thêm `run_heartbeat_cycle` - hàm
THUẦN (không async, testable độc lập không cần event loop) chạy 1 vòng
heartbeat cho toàn bộ máy đủ điều kiện (`next_heartbeat_at IS NULL OR
next_heartbeat_at <= now`). Tái dùng nguyên trạng `resolve_driver`
(D-006/D-012, `app/drivers/`, KHÔNG viết lại) + `driver.get_status()`,
theo đúng pattern try/except đã có ở
`app/printers/service.py::list_printers` (1 máy lỗi không chặn vòng
lặp của máy khác - xem `docs/State_E1-4_v3.md` mục "CHUNK KẾ TIẾP CẦN
CHẠY" cho phạm vi chi tiết). Vòng lặp `asyncio` gọi lại hàm này định kỳ
+ wiring vào FastAPI `lifespan` thuộc chunk C3
(`app/heartbeat/scheduler.py`), KHÔNG thuộc chunk này.

`OFFLINE` khi heartbeat thất bại (Quyết định 7): tái dùng đúng giá trị
canonical `OFFLINE` đã có (D-013), cùng cơ chế map lỗi kết nối ->
`OFFLINE` đã có ở `list_printers` (`MoonrakerClientError` -> `OFFLINE`)
- không thêm giá trị status mới, không thêm cột "online/offline" riêng.

Không đụng `app/printers/service.py`/`router.py` (đã khoá - Quyết định
6): `list_printers` giữ nguyên hành vi poll-on-request hiện có, không
đọc/ghi 2 cột mới, không bị ảnh hưởng bởi backoff của heartbeat
scheduler.
"""

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
    """Công thức backoff cấp số nhân đã chốt ở C1 (Quyết định 5) - chặn
    trần ở `DEFAULT_BACKOFF_MAX_SECONDS`."""
    interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS * (
        DEFAULT_BACKOFF_MULTIPLIER**consecutive_failures
    )
    return min(interval, DEFAULT_BACKOFF_MAX_SECONDS)

def run_heartbeat_cycle(db_path: str = DEFAULT_DB_PATH) -> None:
    """Chạy 1 vòng heartbeat cho tất cả máy đủ điều kiện
    (`next_heartbeat_at IS NULL OR next_heartbeat_at <= now`). Hàm THUẦN
    (không async) - vòng lặp `asyncio` gọi lại hàm này định kỳ thuộc
    chunk C3 (`app/heartbeat/scheduler.py`).

    Với mỗi máy: `resolve_driver(...)` (tái dùng nguyên trạng, KHÔNG viết
    lại - D-006/D-012) + `driver.get_status()`, cùng pattern try/except
    đã có ở `list_printers` (1 máy lỗi không chặn vòng lặp của máy
    khác).

    - Thành công: cập nhật `status` (canonical, D-013) + reset
      `consecutive_heartbeat_failures = 0` + `next_heartbeat_at = now +
      DEFAULT_HEARTBEAT_INTERVAL_SECONDS`.
    - Thất bại (`MoonrakerClientError`, cùng exception `list_printers`
      đã dùng): `status = OFFLINE` (Quyết định 7) + tăng
      `consecutive_heartbeat_failures` thêm 1 + tính lại
      `next_heartbeat_at` theo công thức backoff.

    Không raise cho lỗi kết nối của từng máy riêng lẻ (map sang
    `OFFLINE`, không phải exception) - nhất quán `list_printers`.
    """
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
