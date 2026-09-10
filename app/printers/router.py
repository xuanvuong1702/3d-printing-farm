"""
FastAPI `APIRouter` cho domain "Printer Registry" (E1-1/C1, E1-2/C1,
E1-3/C1) — mount `POST /printers`, `GET /printers`,
`PATCH /printers/{printer_id}`, `DELETE /printers/{printer_id}`.

Router chỉ chịu trách nhiệm map exception/giá trị trả về nghiệp vụ
(`service.py`) sang HTTP status code đúng AC gốc E1-1 ("validate kết
nối được tới Moonraker trước khi lưu" → lỗi kết nối trả HTTP rõ ràng,
không phải 500 chung chung; IP trùng → 409 Conflict, không phải 500) —
không chứa logic nghiệp vụ nào khác.

`GET /printers` (E1-2) không map exception nào riêng — `list_printers`
đã tự bắt `MoonrakerClientError` cho từng máy và map sang `OFFLINE`
(xem `app/printers/service.py`), nên không có lỗi nghiệp vụ nào lộ ra
tới tầng router ở đây.

`PATCH`/`DELETE /printers/{printer_id}` (E1-3) map `None`/chuỗi kết
quả của `update_printer`/`delete_printer` sang `404`/`409` — không
dùng exception cho nhánh nghiệp vụ "có job active" (xem
`docs/State_E1-3_v2.md`).
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, status

from app.printers.schemas import (
    PrinterCreateRequest,
    PrinterResponse,
    PrinterUpdateRequest,
)
from app.printers.service import (
    PrinterAlreadyExistsError,
    PrinterConnectionError,
    delete_printer,
    list_printers,
    register_printer,
    update_printer,
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

@router.patch("/printers/{printer_id}", response_model=PrinterResponse)
def patch_printer(printer_id: int, request: PrinterUpdateRequest) -> PrinterResponse:
    """Sửa `name`/`model`/`api_key` của 1 máy đã đăng ký (AC gốc E1-3 —
    xem `docs/State_E1-3_v2.md` cho rationale phạm vi: KHÔNG cho sửa
    `ip`/`moonraker_port`, body rỗng là no-op hợp lệ)."""
    result = update_printer(printer_id, request)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.delete("/printers/{printer_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_printer(printer_id: int) -> None:
    """Xoá cứng 1 máy in, chặn khi đang có job active (AC gốc E1-3 —
    "có job active" = `status` `PRINTING`/`PAUSED`, D-013/D-010)."""
    result = delete_printer(printer_id)
    if result == "not_found":
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    if result == "has_active_job":
        raise HTTPException(
            status_code=409,
            detail="Máy đang có job active (PRINTING/PAUSED), không thể xoá.",
        )
    if result == "has_related_records":
        raise HTTPException(
            status_code=409,
            detail="Máy còn dữ liệu jobs/job_history liên quan, không thể xoá.",
        )
