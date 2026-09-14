
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, status

from app.db.migrate import DEFAULT_DB_PATH
from app.moonraker.http_client import get_spoolman_spool
from app.spools.schemas import SpoolReport
from app.spools.service import build_spool_report

router = APIRouter()

_SELECT_PRINTER_CONNECTION_SQL = (
    "SELECT ip, moonraker_port, api_key FROM printers WHERE id = ?"
)

@router.get("/spools/{spool_id}", response_model=SpoolReport)
def get_spool_report(spool_id: str, printer_id: int) -> SpoolReport:
    connection = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        printer_row = connection.execute(
            _SELECT_PRINTER_CONNECTION_SQL, (printer_id,)
        ).fetchone()
        if printer_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy máy in id={printer_id}.",
            )
        ip, moonraker_port, api_key = printer_row

        spoolman_response = get_spoolman_spool(
            ip, spool_id, port=moonraker_port, api_key=api_key
        )
        error = spoolman_response.get("error")
        if error is not None:
            if error.get("status_code") == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Không tìm thấy spool id={spool_id} trên Spoolman.",
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Lỗi gọi Spoolman: {error.get('message')}",
            )

        return build_spool_report(
            connection, printer_id, spool_id, spoolman_response
        )
    finally:
        connection.close()
