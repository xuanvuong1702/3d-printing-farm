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
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.heartbeat.scheduler import start_heartbeat_scheduler, stop_heartbeat_scheduler
from app.printers.router import router as printers_router

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    heartbeat_task = start_heartbeat_scheduler()
    try:
        yield
    finally:
        await stop_heartbeat_scheduler(heartbeat_task)

app = FastAPI(title="QIDI Print Farm Orchestration Service", lifespan=lifespan)
app.include_router(printers_router)

@app.get("/")
def read_root() -> dict:
    """Health/hello-world tối thiểu — xác nhận service chạy được qua systemd (AC của E0-1)."""
    return {"status": "ok"}

