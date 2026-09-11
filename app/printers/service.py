
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
       power_device_name, status, is_held, created_at, updated_at
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

class PrinterPowerNotSupportedError(Exception):
    pass

class PrinterPowerNotConfiguredError(Exception):
    pass

class PrinterNotHeldError(Exception):
    pass

class UnsupportedFileTypeError(Exception):
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
        _power_device_name,
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

def emergency_stop_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    return _run_print_command(
        printer_id, db_path, lambda driver: driver.emergency_stop()
    )

def _run_power_command(
    printer_id: int, action: str, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        capabilities_json = row[8]
        power_device_name = row[9]
        capabilities: List[str] = json.loads(capabilities_json)
        if "power" not in capabilities:
            raise PrinterPowerNotSupportedError(
                f"Máy id={printer_id} không có capability 'power', không hỗ trợ "
                "bật/tắt nguồn từ xa."
            )
        if power_device_name is None:
            raise PrinterPowerNotConfiguredError(
                f"Máy id={printer_id} có capability 'power' nhưng chưa cấu hình "
                "power_device_name (PATCH /printers/{printer_id})."
            )

        driver = _resolve_driver_for_row(row)
        try:
            driver.set_power(power_device_name, action)
        except MoonrakerClientError as exc:
            raise PrinterCommandError(
                f"Lỗi khi gọi lệnh Power API trên máy id={printer_id}: {exc}"
            ) from exc

        if action == "off":
            try:
                canonical_status = driver.get_status().canonical_status
            except MoonrakerClientError:

                canonical_status = "OFFLINE"
        else:
            canonical_status = driver.get_status().canonical_status

        connection.execute(_UPDATE_PRINTER_STATUS_SQL, (canonical_status, printer_id))
        connection.commit()
        updated_row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(updated_row)

def power_on_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    return _run_power_command(printer_id, "on", db_path)

def power_off_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    return _run_power_command(printer_id, "off", db_path)

_UPDATE_PRINTER_IS_HELD_SQL = """
UPDATE printers
SET is_held = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

_GCODE_EXTENSION = ".gcode"

_INSERT_JOB_UPLOADING_SQL = """
INSERT INTO jobs (printer_id, filename, status) VALUES (?, ?, 'uploading')
"""

_SELECT_JOB_BY_ID_SQL = """
SELECT id, printer_id, filename, status, priority, file_size_bytes,
       estimated_print_seconds, created_at, updated_at
FROM jobs WHERE id = ?
"""

_UPDATE_JOB_QUEUED_SQL = """
UPDATE jobs
SET status = 'queued', file_size_bytes = ?, estimated_print_seconds = ?,
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

_UPDATE_JOB_FAILED_SQL = """
UPDATE jobs
SET status = 'failed', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

def upload_file_to_printer(
    printer_id: int,
    filename: str,
    file_content: bytes,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[dict]:
    if not filename.lower().endswith(_GCODE_EXTENSION):
        raise UnsupportedFileTypeError(
            f"File {filename!r} không phải G-code (.gcode) - từ chối upload."
        )

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        driver = _resolve_driver_for_row(row)

        cursor = connection.execute(_INSERT_JOB_UPLOADING_SQL, (printer_id, filename))
        connection.commit()
        job_id = cursor.lastrowid

        try:
            driver.upload_file(filename, file_content)
            metadata = driver.get_file_metadata(filename)
        except MoonrakerClientError as exc:
            connection.execute(_UPDATE_JOB_FAILED_SQL, (job_id,))
            connection.commit()
            raise PrinterCommandError(
                f"Lỗi khi upload file lên máy id={printer_id}: {exc}"
            ) from exc

        file_size_bytes = metadata.get("size")
        estimated_time = metadata.get("estimated_time")
        estimated_print_seconds = (
            int(estimated_time) if estimated_time is not None else None
        )

        connection.execute(
            _UPDATE_JOB_QUEUED_SQL,
            (file_size_bytes, estimated_print_seconds, job_id),
        )
        connection.commit()
        job_row = connection.execute(_SELECT_JOB_BY_ID_SQL, (job_id,)).fetchone()
    finally:
        connection.close()

    return _job_row_to_dict(job_row)

def _job_row_to_dict(row: tuple) -> dict:
    (
        id_,
        printer_id,
        filename,
        status,
        priority,
        file_size_bytes,
        estimated_print_seconds,
        created_at,
        updated_at,
    ) = row
    return {
        "id": id_,
        "printer_id": printer_id,
        "filename": filename,
        "status": status,
        "priority": priority,
        "file_size_bytes": file_size_bytes,
        "estimated_print_seconds": estimated_print_seconds,
        "created_at": created_at,
        "updated_at": updated_at,
    }

def enqueue_job_to_moonraker_queue(
    printer_id: int,
    filename: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[bool]:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        capabilities: List[str] = json.loads(row[8])
        if "job_queue" not in capabilities:
            return False

        driver = _resolve_driver_for_row(row)
        try:
            driver.enqueue_job([filename])
        except MoonrakerClientError as exc:
            raise PrinterCommandError(
                f"Lỗi khi đẩy job vào hàng đợi Moonraker của máy "
                f"id={printer_id}: {exc}"
            ) from exc
    finally:
        connection.close()

    return True

def confirm_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        is_held = row[11]
        if not is_held:
            raise PrinterNotHeldError(
                f"Máy id={printer_id} không đang ở trạng thái is_held, "
                "không có gì để xác nhận."
            )

        connection.execute(_UPDATE_PRINTER_IS_HELD_SQL, (0, printer_id))
        connection.commit()
        updated_row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(updated_row)

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
        power_device_name,
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
        power_device_name=power_device_name,
        status=status,
        is_held=bool(is_held),
        created_at=created_at,
        updated_at=updated_at,
    )
