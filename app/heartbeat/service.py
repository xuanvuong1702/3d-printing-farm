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

Phạm vi chunk E3-4/C1 (chunk này - xem `docs/State_E3-4_v1.md` mục
"Quyết định phạm vi" điểm 1-5 cho rationale đầy đủ): CHỈ thêm hàm thuần
`compute_updated_is_held` tính lại `printers.is_held` (D-010) dựa trên
`old_status`/`new_status`/`is_held` cũ/`filename` hiện có. Hàm này CHƯA
được gọi ở đâu cả (chưa wiring vào `run_heartbeat_cycle`, chưa đọc/ghi
cột `is_held` trong SQL) - việc đó thuộc chunk C2. `run_heartbeat_cycle`
ở chunk này giữ NGUYÊN VẸN hành vi hiện có (đã khoá từ E1-4), không sửa
gì trong hàm đó hay 2 câu SQL `_SELECT_DUE_PRINTERS_SQL`/
`_UPDATE_HEARTBEAT_RESULT_SQL`.
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

_HOLD_TRIGGER_STATUSES = frozenset({"FINISHED", "ERROR"})

_HOLD_AUTO_CLEAR_STATUS = "PRINTING"

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

def compute_updated_is_held(
    *,
    old_status: str,
    new_status: str,
    is_held: bool,
    filename: "str | None",
) -> bool:
    """Tính lại `printers.is_held` (D-010) cho 1 máy tại 1 vòng heartbeat
    (E3-4/C1) - xem `docs/State_E3-4_v1.md` mục "Quyết định phạm vi"
    điểm 2-5 cho rationale đầy đủ. Hàm THUẦN - không đụng DB/HTTP, chỉ
    tính giá trị mới từ input đã có sẵn tại vòng heartbeat đó (wiring
    vào `run_heartbeat_cycle`, đọc `is_held` cũ + ghi lại giá trị mới,
    thuộc chunk C2 - KHÔNG làm ở đây).

    - Nếu `is_held` hiện tại là `False`: set `True` khi và chỉ khi CẢ
      3 điều kiện đều đúng:
      1. Có một **chuyển trạng thái thật** (`new_status != old_status`)
         - tránh set lặp lại mỗi vòng heartbeat kế tiếp khi máy đứng
         yên ở `FINISHED`/`ERROR` nhiều lượt liền (chưa có gì thay đổi
         thật so với lần trước, không phải một sự kiện "chuyển" mới).
      2. `new_status` thuộc `_HOLD_TRIGGER_STATUSES` (`FINISHED`/
         `ERROR`, điểm 3 - đúng 2 giá trị AC gốc E3-4 nêu, không mở
         rộng thêm).
      3. Máy đang có job active tại thời điểm đó (điểm 2 - suy ra từ
         `filename is not None`, KHÔNG dùng bảng `jobs` vì bảng đó
         luôn rỗng do Epic 4 chưa triển khai).
    - Nếu `is_held` hiện tại là `True`: CHỈ tự gỡ về `False` khi phát
      hiện đúng ngoại lệ whitelist DUY NHẤT (điểm 5, nguyên tắc #1
      `CLAUDE.md`): `new_status == _HOLD_AUTO_CLEAR_STATUS` ("PRINTING")
      VÀ `filename is not None` (máy đã hồi phục về đang in với job
      thật - false alarm do rớt mạng thoáng qua). Mọi trường hợp khác
      giữ nguyên `True` - endpoint xác nhận vận hành viên (chunk C3,
      điểm 6) là đường DUY NHẤT khác được phép gỡ `is_held`.
    """
    if is_held:
        auto_recovered = (
            new_status == _HOLD_AUTO_CLEAR_STATUS and filename is not None
        )
        return not auto_recovered

    is_real_transition = new_status != old_status
    reached_hold_status = new_status in _HOLD_TRIGGER_STATUSES
    has_active_job = filename is not None
    return is_real_transition and reached_hold_status and has_active_job

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
