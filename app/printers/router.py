
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
    try:
        return register_printer(request)
    except PrinterConnectionError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrinterAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/printers", response_model=List[PrinterResponse])
def get_printers() -> List[PrinterResponse]:
    return list_printers()
