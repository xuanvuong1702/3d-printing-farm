
from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from app.db.migrate import DEFAULT_DB_PATH
from app.drivers import resolve_driver
from app.moonraker.http_client import MoonrakerClientError
from app.printers.schemas import (
    PrinterCreateRequest,
    PrinterResponse,
    PrinterUpdateRequest,
)

_INSERT_PRINTER_SQL = """
INSERT INTO printers (
    name, ip, moonraker_port, model, api_key,
    moonraker_version, klipper_version, capabilities
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_PRINTER_BY_ID_SQL = """
SELECT id, name, ip, moonraker_port, model, api_key,
       moonraker_version, klipper_version, capabilities,
       status, is_held, created_at, updated_at
FROM printers WHERE id = ?
"""

_SELECT_ALL_PRINTER_CONN_INFO_SQL = """
SELECT id, ip, moonraker_port, model, api_key, klipper_version
FROM printers
"""

_UPDATE_PRINTER_STATUS_SQL = """
UPDATE printers
SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

class PrinterConnectionError(Exception):
    pass

class PrinterAlreadyExistsError(Exception):
    pass

class PrinterCommandError(Exception):
    pass

def register_printer(
    request: PrinterCreateRequest, db_path: str = DEFAULT_DB_PATH
) -> PrinterResponse:
    driver = resolve_driver(
        model=request.model,
        firmware_version=None,
        host=request.ip,
        port=request.moonraker_port,
        api_key=request.api_key,
    )

    try:
        server_info = driver.get_server_info()
        printer_info = driver.get_printer_info()
    except MoonrakerClientError as exc:
        raise PrinterConnectionError(
            f"Không kết nối/xác nhận được Moonraker tại "
            f"{request.ip}:{request.moonraker_port}: {exc}"
        ) from exc

    moonraker_version: Optional[str] = server_info.get("moonraker_version")
    capabilities: List[str] = server_info.get("components", [])
    klipper_version: Optional[str] = printer_info.get("software_version")
    capabilities_json = json.dumps(capabilities)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL,
                (
                    request.name,
                    request.ip,
                    request.moonraker_port,
                    request.model,
                    request.api_key,
                    moonraker_version,
                    klipper_version,
                    capabilities_json,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PrinterAlreadyExistsError(
                f"IP {request.ip!r} đã được đăng ký trước đó."
            ) from exc

        connection.commit()
        row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (cursor.lastrowid,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(row)

def list_printers(db_path: str = DEFAULT_DB_PATH) -> List[PrinterResponse]:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        conn_rows = connection.execute(_SELECT_ALL_PRINTER_CONN_INFO_SQL).fetchall()

        responses: List[PrinterResponse] = []
        for printer_id, ip, moonraker_port, model, api_key, klipper_version in conn_rows:
            driver = resolve_driver(
                model=model,
                firmware_version=klipper_version,
                host=ip,
                port=moonraker_port,
                api_key=api_key,
            )
            try:
                canonical_status = driver.get_status().canonical_status
            except MoonrakerClientError:

                canonical_status = "OFFLINE"

            connection.execute(
                _UPDATE_PRINTER_STATUS_SQL, (canonical_status, printer_id)
            )
            connection.commit()

            updated_row = connection.execute(
                _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
            ).fetchone()
            responses.append(_row_to_response(updated_row))
    finally:
        connection.close()

    return responses

def update_printer(
    printer_id: int,
    request: PrinterUpdateRequest,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PrinterResponse]:
    fields = request.model_dump(exclude_unset=True)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        existing = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
        if existing is None:
            return None

        if fields:
            set_clauses = ", ".join(f"{column} = ?" for column in fields)
            update_sql = (
                f"UPDATE printers SET {set_clauses}, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                "WHERE id = ?"
            )
            connection.execute(update_sql, (*fields.values(), printer_id))
            connection.commit()
            row = connection.execute(
                _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
            ).fetchone()
        else:
            row = existing
    finally:
        connection.close()

    return _row_to_response(row)

def delete_printer(printer_id: int, db_path: str = DEFAULT_DB_PATH) -> str:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(
            "SELECT status FROM printers WHERE id = ?", (printer_id,)
        ).fetchone()
        if row is None:
            return "not_found"

        (current_status,) = row
        if current_status in ("PRINTING", "PAUSED"):
            return "has_active_job"

        try:
            connection.execute("DELETE FROM printers WHERE id = ?", (printer_id,))
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            return "has_related_records"
    finally:
        connection.close()

    return "deleted"

def _resolve_driver_for_row(row: tuple):
    (
        _id,
        _name,
        ip,
        moonraker_port,
        model,
        api_key,
        _moonraker_version,
        klipper_version,
        _capabilities_json,
        _status,
        _is_held,
        _created_at,
        _updated_at,
    ) = row
    return resolve_driver(
        model=model,
        firmware_version=klipper_version,
        host=ip,
        port=moonraker_port,
        api_key=api_key,
    )

def _run_print_command(
    printer_id: int, db_path: str, command
) -> Optional[PrinterResponse]:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        driver = _resolve_driver_for_row(row)
        try:
            command(driver)
            canonical_status = driver.get_status().canonical_status
        except MoonrakerClientError as exc:
            raise PrinterCommandError(
                f"Lỗi khi gọi lệnh điều khiển job trên máy id={printer_id}: {exc}"
            ) from exc

        connection.execute(_UPDATE_PRINTER_STATUS_SQL, (canonical_status, printer_id))
        connection.commit()
        updated_row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(updated_row)

def start_print(
    printer_id: int,
    filename: str,
    file_content: bytes,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PrinterResponse]:
    return _run_print_command(
        printer_id,
        db_path,
        lambda driver: driver.upload_and_print(filename, file_content),
    )

def pause_print(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    return _run_print_command(printer_id, db_path, lambda driver: driver.pause_job())

def resume_print(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    return _run_print_command(printer_id, db_path, lambda driver: driver.resume_job())

def cancel_print(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    return _run_print_command(printer_id, db_path, lambda driver: driver.cancel_job())

def _row_to_response(row: tuple) -> PrinterResponse:
    (
        id_,
        name,
        ip,
        moonraker_port,
        model,
        api_key,
        moonraker_version,
        klipper_version,
        capabilities_json,
        status,
        is_held,
        created_at,
        updated_at,
    ) = row
    return PrinterResponse(
        id=id_,
        name=name,
        ip=ip,
        moonraker_port=moonraker_port,
        model=model,
        api_key=api_key,
        moonraker_version=moonraker_version,
        klipper_version=klipper_version,
        capabilities=json.loads(capabilities_json),
        status=status,
        is_held=bool(is_held),
        created_at=created_at,
        updated_at=updated_at,
    )
