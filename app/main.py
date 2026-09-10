
from fastapi import FastAPI

from app.printers.router import router as printers_router

app = FastAPI(title="QIDI Print Farm Orchestration Service")
app.include_router(printers_router)

@app.get("/")
def read_root() -> dict:
    return {"status": "ok"}
