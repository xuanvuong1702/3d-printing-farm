
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import AsyncIterator, Awaitable, Callable, List, Optional

from app.db.migrate import DEFAULT_DB_PATH
from app.realtime.schemas import PrinterRealtimeResponse
from app.realtime.state import RealtimePrinterState, RealtimeStateStore

REALTIME_STREAM_INTERVAL_SECONDS = 1.0

_SELECT_PRINTER_ID_NAME_STATUS_SQL = """
SELECT id, name, status FROM printers WHERE id = ?
"""

_SELECT_ALL_PRINTER_ID_NAME_STATUS_SQL = """
SELECT id, name, status FROM printers
"""

def _merge_row_with_realtime_state(
    printer_id: int,
    name: str,
    db_status: str,
    realtime_state: Optional[RealtimePrinterState],
) -> PrinterRealtimeResponse:
    if realtime_state is None:
        return PrinterRealtimeResponse(
            id=printer_id,
            name=name,
            realtime_connected=False,
            canonical_status=db_status,
        )

    return PrinterRealtimeResponse(
        id=printer_id,
        name=name,
        realtime_connected=True,
        canonical_status=realtime_state.canonical_status,
        progress_percent=realtime_state.progress_percent,
        time_remaining_seconds=realtime_state.time_remaining_seconds,
        filename=realtime_state.filename,
        extruder_temp=realtime_state.extruder_temp,
        extruder_target=realtime_state.extruder_target,
        bed_temp=realtime_state.bed_temp,
        bed_target=realtime_state.bed_target,
        updated_at=realtime_state.updated_at,
    )

async def get_printer_realtime(
    printer_id: int,
    store: RealtimeStateStore,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PrinterRealtimeResponse]:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            _SELECT_PRINTER_ID_NAME_STATUS_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    db_id, name, db_status = row
    realtime_state = await store.get(db_id)
    return _merge_row_with_realtime_state(db_id, name, db_status, realtime_state)

async def list_printers_realtime(
    store: RealtimeStateStore,
    db_path: str = DEFAULT_DB_PATH,
) -> List[PrinterRealtimeResponse]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(_SELECT_ALL_PRINTER_ID_NAME_STATUS_SQL).fetchall()
    finally:
        connection.close()

    all_states = await store.get_all()

    return [
        _merge_row_with_realtime_state(
            printer_id, name, db_status, all_states.get(printer_id)
        )
        for printer_id, name, db_status in rows
    ]

def _format_sse_data_line(snapshot: List[PrinterRealtimeResponse]) -> str:
    payload = json.dumps([response.model_dump() for response in snapshot])
    return f"data: {payload}\n\n"

async def realtime_sse_event_generator(
    store: RealtimeStateStore,
    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
    db_path: str = DEFAULT_DB_PATH,
    interval_seconds: float = REALTIME_STREAM_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        snapshot = await list_printers_realtime(store, db_path=db_path)
        yield _format_sse_data_line(snapshot)
        await asyncio.sleep(interval_seconds)
