
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
    try:
        return register_printer(request)
    except PrinterConnectionError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrinterAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/printers", response_model=List[PrinterResponse])
def get_printers() -> List[PrinterResponse]:
    return list_printers()

@router.patch("/printers/{printer_id}", response_model=PrinterResponse)
def patch_printer(printer_id: int, request: PrinterUpdateRequest) -> PrinterResponse:
    result = update_printer(printer_id, request)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.delete("/printers/{printer_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_printer(printer_id: int) -> None:
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
