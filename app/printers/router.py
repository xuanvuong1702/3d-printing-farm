
from __future__ import annotations

from typing import List

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from app.printers.auto_assign import NoAvailablePrinterError, auto_assign_and_upload
from app.printers.schemas import (
    EmergencyStopRequest,
    JobResponse,
    PrinterCreateRequest,
    PrinterResponse,
    PrinterUpdateRequest,
)
from app.printers.service import (
    PrinterAlreadyExistsError,
    PrinterCommandError,
    PrinterConnectionError,
    PrinterNotHeldError,
    PrinterPowerNotConfiguredError,
    PrinterPowerNotSupportedError,
    UnsupportedFileTypeError,
    cancel_print,
    confirm_printer,
    delete_printer,
    emergency_stop_printer,
    enqueue_job_to_moonraker_queue,
    list_printers,
    pause_print,
    power_off_printer,
    power_on_printer,
    register_printer,
    resume_print,
    start_print,
    update_printer,
    upload_file_to_printer,
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

@router.post("/printers/{printer_id}/print/start", response_model=PrinterResponse)
async def start_printer_job(printer_id: int, file: UploadFile = File(...)) -> PrinterResponse:
    file_content = await file.read()
    try:
        result = start_print(printer_id, file.filename, file_content)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/print/pause", response_model=PrinterResponse)
def pause_printer_job(printer_id: int) -> PrinterResponse:
    try:
        result = pause_print(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/print/resume", response_model=PrinterResponse)
def resume_printer_job(printer_id: int) -> PrinterResponse:
    try:
        result = resume_print(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/print/cancel", response_model=PrinterResponse)
def cancel_printer_job(printer_id: int) -> PrinterResponse:
    try:
        result = cancel_print(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post(
    "/printers/{printer_id}/emergency_stop", response_model=PrinterResponse
)
def emergency_stop_printer_endpoint(
    printer_id: int, request: EmergencyStopRequest
) -> PrinterResponse:
    if not request.confirm:
        raise HTTPException(
            status_code=422,
            detail="Cần xác nhận (confirm=true) trước khi gửi lệnh Emergency Stop.",
        )
    try:
        result = emergency_stop_printer(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/confirm", response_model=PrinterResponse)
def confirm_printer_endpoint(printer_id: int) -> PrinterResponse:
    try:
        result = confirm_printer(printer_id)
    except PrinterNotHeldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/power/on", response_model=PrinterResponse)
def power_on_printer_endpoint(printer_id: int) -> PrinterResponse:
    try:
        result = power_on_printer(printer_id)
    except (PrinterPowerNotSupportedError, PrinterPowerNotConfiguredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/power/off", response_model=PrinterResponse)
def power_off_printer_endpoint(printer_id: int) -> PrinterResponse:
    try:
        result = power_off_printer(printer_id)
    except (PrinterPowerNotSupportedError, PrinterPowerNotConfiguredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post(
    "/printers/auto-assign/files",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def auto_assign_printer_file(
    request: Request, file: UploadFile = File(...)
) -> JobResponse:
    file_content = await file.read()
    try:
        result = await auto_assign_and_upload(
            filename=file.filename,
            file_content=file_content,
            store=request.app.state.realtime_store,
        )
    except NoAvailablePrinterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JobResponse(**result)

@router.post(
    "/printers/{printer_id}/files",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_printer_file(
    printer_id: int, file: UploadFile = File(...)
) -> JobResponse:
    file_content = await file.read()
    try:
        result = upload_file_to_printer(printer_id, file.filename, file_content)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )

    try:
        enqueue_job_to_moonraker_queue(printer_id, file.filename)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return JobResponse(**result)
