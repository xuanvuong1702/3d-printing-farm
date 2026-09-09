"""
QIDI Print Farm Orchestration Service — entrypoint.

Phạm vi chunk E0-1/C1: CHỈ dựng khung chạy được (FastAPI + Uvicorn + systemd).
Không thêm route/model/logic nghiệp vụ nào khác ở đây — schema DB thuộc
E0-4, đăng ký máy thuộc E1-1, v.v. Xem Backlog.md mục Epic 0 và
Decisions.md cho các quyết định kiến trúc liên quan (chưa có D nào áp
dụng trực tiếp tới chunk này, xem State_E0-1_v1.md).
"""

from fastapi import FastAPI

app = FastAPI(title="QIDI Print Farm Orchestration Service")

@app.get("/")
def read_root() -> dict:
    """Health/hello-world tối thiểu — xác nhận service chạy được qua systemd (AC của E0-1)."""
    return {"status": "ok"}
