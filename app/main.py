
from fastapi import FastAPI

app = FastAPI(title="QIDI Print Farm Orchestration Service")

@app.get("/")
def read_root() -> dict:
    return {"status": "ok"}
