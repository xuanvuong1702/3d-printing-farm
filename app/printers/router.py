"""
FastAPI `APIRouter` cho domain "Printer Registry" (E1-1/C1, E1-2/C1) —
mount `POST /printers`, `GET /printers`.

Router chỉ chịu trách nhiệm map exception nghiệp vụ (`service.py`) sang
HTTP status code đúng AC gốc E1-1 ("validate kết nối được tới Moonraker
trước khi lưu" → lỗi kết nối trả HTTP rõ ràng, không phải 500 chung
chung; IP trùng → 409 Conflict, không phải 500) — không chứa logic
nghiệp vụ nào khác.

`GET /printers` (E1-2) không map exception nào riêng — `list_printers`
đã tự bắt `MoonrakerClientError` cho từng máy và map sang `OFFLINE`
(xem `app/printers/service.py`), nên không có lỗi nghiệp vụ nào lộ ra
tới tầng router ở đây.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, status

from app.printers.schemas import PrinterCreateRequest, PrinterResponse
from app.printers.service import (
    PrinterAlreadyExistsError,
    PrinterConnectionError,
    list_printers,
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

@router.get("/printers", response_model=List[PrinterResponse])
def get_printers() -> List[PrinterResponse]:
    """Danh sách máy đã đăng ký kèm trạng thái poll-on-request qua HTTP
    (AC gốc E1-2 — xem `docs/State_E1-2_v2.md` cho rationale phạm vi)."""
    return list_printers()
