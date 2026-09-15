
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.dispatch.scheduler import start_dispatch_scheduler, stop_dispatch_scheduler
from app.heartbeat.scheduler import start_heartbeat_scheduler, stop_heartbeat_scheduler
from app.history.scheduler import start_history_scheduler, stop_history_scheduler
from app.history.service import get_job_history_for_printer
from app.printers.router import router as printers_router
from app.realtime.alerts import start_alert_watcher, stop_alert_watcher
from app.realtime.pool import start_websocket_pool, stop_websocket_pool
from app.realtime.router import router as realtime_router
from app.realtime.service import get_printer_realtime, list_printers_realtime
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

_BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")

@app.get("/")
def read_root() -> dict:
    return {"status": "ok"}

@app.get("/dashboard")
async def dashboard(request: Request):
    printers = await list_printers_realtime(request.app.state.realtime_store)
    printers_json = json.dumps([printer.model_dump() for printer in printers])
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "page_title": "Dashboard",
            "printers": printers,
            "printers_json": printers_json,
        },
    )

@app.get("/printers/{printer_id}/detail")
async def printer_detail(request: Request, printer_id: int):
    printer = await get_printer_realtime(printer_id, request.app.state.realtime_store)
    if printer is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    history = get_job_history_for_printer(printer_id)
    return templates.TemplateResponse(
        request,
        "printer_detail.html",
        {
            "page_title": printer.name,
            "printer": printer,
            "history": history,
        },
    )

