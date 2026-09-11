
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.realtime.schemas import PrinterRealtimeResponse
from app.realtime.service import (
    get_printer_realtime,
    list_printers_realtime,
    realtime_sse_event_generator,
)

router = APIRouter()

@router.get("/printers/{printer_id}/realtime", response_model=PrinterRealtimeResponse)
async def get_printer_realtime_snapshot(
    printer_id: int, request: Request
) -> PrinterRealtimeResponse:
    result = await get_printer_realtime(printer_id, request.app.state.realtime_store)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy máy in id={printer_id}.",
        )
    return result

@router.get("/printers/realtime", response_model=List[PrinterRealtimeResponse])
async def list_printers_realtime_snapshot(
    request: Request,
) -> List[PrinterRealtimeResponse]:
    return await list_printers_realtime(request.app.state.realtime_store)

@router.get("/printers/realtime/stream")
async def stream_printers_realtime(request: Request) -> StreamingResponse:
    return StreamingResponse(
        realtime_sse_event_generator(
            request.app.state.realtime_store,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
    )
