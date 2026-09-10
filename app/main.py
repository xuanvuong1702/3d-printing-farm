
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
    return {"status": "ok"}

