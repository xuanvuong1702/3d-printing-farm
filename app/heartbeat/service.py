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
"""

from __future__ import annotations

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0

DEFAULT_BACKOFF_MULTIPLIER = 2.0

DEFAULT_BACKOFF_MAX_SECONDS = 300.0
