"""
FastAPI `APIRouter` cho domain "Printer Registry" (E1-1/C1) — mount
`POST /printers`.

Router chỉ chịu trách nhiệm map exception nghiệp vụ (`service.py`) sang
HTTP status code đúng AC gốc E1-1 ("validate kết nối được tới Moonraker
trước khi lưu" → lỗi kết nối trả HTTP rõ ràng, không phải 500 chung
chung; IP trùng → 409 Conflict, không phải 500) — không chứa logic
nghiệp vụ nào khác.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.printers.schemas import PrinterCreateRequest, PrinterResponse
from app.printers.service import (
    PrinterAlreadyExistsError,
    PrinterConnectionError,
    register_printer,
)

router = APIRouter()

@router.post(
    "/printers",
    response_model=PrinterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_printer(request: PrinterCreateRequest) -> PrinterResponse:
    """Đăng ký 1 máy in mới bằng IP + tên (AC gốc E1-1)."""
    try:
        return register_printer(request)
    except PrinterConnectionError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrinterAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
