
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.dispatch.scheduler import start_dispatch_scheduler, stop_dispatch_scheduler
from app.heartbeat.scheduler import start_heartbeat_scheduler, stop_heartbeat_scheduler
from app.history.scheduler import start_history_scheduler, stop_history_scheduler
from app.printers.router import router as printers_router
from app.realtime.alerts import start_alert_watcher, stop_alert_watcher
from app.realtime.pool import start_websocket_pool, stop_websocket_pool
from app.realtime.router import router as realtime_router
from app.realtime.state import RealtimeStateStore
from app.reports.router import router as reports_router
from app.spools.router import router as spools_router

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    heartbeat_task = start_heartbeat_scheduler()
    dispatch_task = start_dispatch_scheduler()
    history_task = start_history_scheduler()
    realtime_store = RealtimeStateStore()
    app.state.realtime_store = realtime_store
    websocket_pool = start_websocket_pool(realtime_store)
    alert_task = start_alert_watcher(realtime_store)
    try:
        yield
    finally:
        await stop_alert_watcher(alert_task)
        await stop_websocket_pool(websocket_pool)
        await stop_history_scheduler(history_task)
        await stop_dispatch_scheduler(dispatch_task)
        await stop_heartbeat_scheduler(heartbeat_task)

app = FastAPI(title="QIDI Print Farm Orchestration Service", lifespan=lifespan)
app.include_router(printers_router)
app.include_router(realtime_router)
app.include_router(reports_router)
app.include_router(spools_router)

@app.get("/")
def read_root() -> dict:
    return {"status": "ok"}

