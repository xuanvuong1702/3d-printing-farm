"""
QIDI Print Farm Orchestration Service — entrypoint.

Phạm vi chunk E0-1/C1: CHỈ dựng khung chạy được (FastAPI + Uvicorn + systemd).
Không thêm route/model/logic nghiệp vụ nào khác ở đây — schema DB thuộc
E0-4, đăng ký máy thuộc E1-1, v.v. Xem Backlog.md mục Epic 0 và
Decisions.md cho các quyết định kiến trúc liên quan (chưa có D nào áp
dụng trực tiếp tới chunk này, xem State_E0-1_v1.md).

Chunk E1-1/C1: include router đầu tiên chạm tầng API thật
(`app/printers/router.py`, domain "Printer Registry") — không đổi
endpoint `/` hiện có.

Chunk E1-4/C3: thêm FastAPI `lifespan` context manager để khởi động/huỷ
heartbeat scheduler nền (`app/heartbeat/scheduler.py`, C3) — CHỈ wiring
(startup: `start_heartbeat_scheduler()`; shutdown:
`stop_heartbeat_scheduler(task)`), KHÔNG đổi route/logic nghiệp vụ hiện
có. Trước chunk này service chưa có `lifespan`/`@app.on_event` nào —
đây là lần đầu áp dụng cơ chế chạy nền, đúng Quyết định 3 đã chốt ở C0
(`docs/State_E1-4_v4.md`).

Chunk E2-1/C3: mở rộng `lifespan` (KHÔNG đổi phần heartbeat đã khoá ở
trên, chỉ thêm) để khởi động/huỷ pool kết nối WebSocket real-time
(`app/realtime/pool.py`, C3, Quyết định 6) — startup:
`start_websocket_pool(store)`; shutdown: `stop_websocket_pool(pool)`.
`RealtimeStateStore` (C1) được tạo 1 lần ở đây và gán vào
`app.state.realtime_store` để các route tương lai (ngoài phạm vi story
này) có thể đọc state real-time mà không cần biến toàn cục.

Chunk E2-2/C2: include router thứ 2 (`app/realtime/router.py`, domain
"giám sát real-time" — 2 endpoint snapshot đọc `app.state.realtime_store`
đã gán ở trên) — KHÔNG đổi `printers_router`/route `/`/`lifespan` hiện
có.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.heartbeat.scheduler import start_heartbeat_scheduler, stop_heartbeat_scheduler
from app.printers.router import router as printers_router
from app.realtime.pool import start_websocket_pool, stop_websocket_pool
from app.realtime.router import router as realtime_router
from app.realtime.state import RealtimeStateStore

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    heartbeat_task = start_heartbeat_scheduler()
    realtime_store = RealtimeStateStore()
    app.state.realtime_store = realtime_store
    websocket_pool = start_websocket_pool(realtime_store)
    try:
        yield
    finally:
        await stop_websocket_pool(websocket_pool)
        await stop_heartbeat_scheduler(heartbeat_task)

app = FastAPI(title="QIDI Print Farm Orchestration Service", lifespan=lifespan)
app.include_router(printers_router)
app.include_router(realtime_router)

@app.get("/")
def read_root() -> dict:
    """Health/hello-world tối thiểu — xác nhận service chạy được qua systemd (AC của E0-1)."""
    return {"status": "ok"}

