"""
Scheduler nền cho heartbeat (E1-4/C3).

Wiring vòng lặp `asyncio` gọi lại `run_heartbeat_cycle`
(`app/heartbeat/service.py`, C2, KHÔNG sửa lại hàm đó — đã khoá) định
kỳ, khởi động/huỷ qua FastAPI `lifespan` (`app/main.py`, wiring only).
Đúng Quyết định 3 đã chốt ở C0 (`docs/State_E1-4_v1.md`/`_v4.md` mục
"Quyết định phạm vi chốt tại chunk C0"): dùng `asyncio.create_task`,
KHÔNG thêm thư viện scheduler ngoài (vd. APScheduler).

Phân biệt 2 khái niệm "interval" dễ nhầm:
- `SCHEDULER_POLL_INTERVAL_SECONDS` (module này): khoảng nghỉ giữa các
  lần vòng lặp scheduler GỌI LẠI `run_heartbeat_cycle` để xem có máy
  nào tới lượt chưa — không gắn với 1 máy cụ thể nào. Đủ ngắn (vài
  giây) để phát hiện "tới lượt" kịp thời mà không tốn CPU/DB liên tục.
- `DEFAULT_HEARTBEAT_INTERVAL_SECONDS` (`app/heartbeat/service.py`,
  C1): khoảng cách giữa 2 lần heartbeat THẬT cho 1 máy cụ thể đang
  khoẻ mạnh, lưu per-máy qua cột `next_heartbeat_at`.
  `run_heartbeat_cycle` tự lọc đúng máy nào đủ điều kiện mỗi lần được
  gọi (SELECT WHERE `next_heartbeat_at IS NULL OR <= now`) — scheduler
  ở module này không cần tự tính máy nào tới lượt, chỉ cần gọi lại đủ
  thường xuyên.

Quyết định kỹ thuật cục bộ chốt tại chunk này (không phải `D-00X`):

**Chạy `run_heartbeat_cycle` qua `asyncio.to_thread`, KHÔNG gọi trực
tiếp trong coroutine**: `run_heartbeat_cycle` là hàm THUẦN đồng bộ
(blocking) — dùng `sqlite3` đồng bộ + `httpx.request` đồng bộ
(`app/moonraker/http_client.py`, D-002 phần 1, KHÔNG phải
`httpx.AsyncClient`). Nếu gọi trực tiếp trong 1 coroutine chạy trên
event loop chính của FastAPI, mỗi vòng heartbeat (có thể gọi HTTP tới
nhiều máy tuần tự) sẽ CHẶN toàn bộ event loop — mọi request khác
(`GET /printers`, `POST /printers`, ...) phải đợi heartbeat chạy xong
mới được xử lý, vi phạm tinh thần "1 máy lỗi/chậm không được ảnh hưởng
phần còn lại của service" đã áp dụng nhất quán từ `list_printers`.
`asyncio.to_thread(run_heartbeat_cycle)` (stdlib, không thêm
dependency, đúng tinh thần tối giản đã áp dụng cho Quyết định 3) chạy
hàm trong 1 thread riêng của threadpool mặc định, nhường event loop
chính rảnh để xử lý request khác trong lúc heartbeat đang chạy — đây
cũng chính là lý do "SQLite lock khi heartbeat + request khác ghi đồng
thời" là 1 rủi ro THẬT cần xác nhận ở chunk này (heartbeat giờ thật sự
chạy song song với request handler, không phải tuần tự giả lập trong
cùng 1 event loop).

Lỗi trong 1 vòng heartbeat (bao gồm cả lỗi không mong đợi, khác với
`MoonrakerClientError` đã được `run_heartbeat_cycle`/C2 tự bắt cho
từng máy) KHÔNG được làm chết hẳn task nền — log lại rồi tiếp tục vòng
lặp ở lần `sleep` kế tiếp, để 1 lỗi bất ngờ (ví dụ DB tạm thời khoá quá
lâu, vượt timeout mặc định của `sqlite3.connect`) không tắt hoàn toàn
cơ chế phát hiện offline cho những lần sau.
"""

from __future__ import annotations

import asyncio
import logging

from app.heartbeat.service import run_heartbeat_cycle

logger = logging.getLogger(__name__)

SCHEDULER_POLL_INTERVAL_SECONDS = 5.0

async def _heartbeat_loop(poll_interval_seconds: float) -> None:
    """Vòng lặp nền: gọi `run_heartbeat_cycle` (qua thread riêng, xem
    docstring module) rồi nghỉ `poll_interval_seconds` trước lần kế
    tiếp. Chạy tới khi bị huỷ (`asyncio.CancelledError`, xem
    `stop_heartbeat_scheduler`)."""
    while True:
        try:
            await asyncio.to_thread(run_heartbeat_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:

            logger.exception(
                "Lỗi không mong đợi trong 1 vòng heartbeat - tiếp tục vòng "
                "lặp ở lần kế tiếp."
            )
        await asyncio.sleep(poll_interval_seconds)

def start_heartbeat_scheduler(
    poll_interval_seconds: float = SCHEDULER_POLL_INTERVAL_SECONDS,
) -> "asyncio.Task[None]":
    """Khởi động task nền chạy `_heartbeat_loop`. Gọi từ FastAPI
    `lifespan` lúc startup (`app/main.py`). Trả về `asyncio.Task` để
    `stop_heartbeat_scheduler` huỷ đúng lúc shutdown."""
    return asyncio.create_task(_heartbeat_loop(poll_interval_seconds))

async def stop_heartbeat_scheduler(task: "asyncio.Task[None]") -> None:
    """Huỷ task nền + đợi huỷ xong hẳn (tránh warning "Task was
    destroyed but it is pending"). Gọi từ FastAPI `lifespan` lúc
    shutdown (`app/main.py`)."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
