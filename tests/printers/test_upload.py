
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db.migrate import run_migrations
from app.printers.service import (
    PrinterCommandError,
    UnsupportedFileTypeError,
    upload_file_to_printer,
)

_GCODE_CONTENT = b"G28\nG1 X10 Y10\n"

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E4-1"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def test_upload_rejects_non_gcode_extension_before_touching_db(tmp_path) -> None:
    never_migrated_db_path = str(tmp_path / "never_migrated.db")

    with pytest.raises(UnsupportedFileTypeError):
        upload_file_to_printer(
            printer_id=1,
            filename="model.stl",
            file_content=b"not gcode",
            db_path=never_migrated_db_path,
        )

    with pytest.raises(UnsupportedFileTypeError):
        upload_file_to_printer(
            printer_id=1,
            filename="MODEL.GCODE.STL",
            file_content=b"not gcode",
            db_path=never_migrated_db_path,
        )

def test_upload_accepts_gcode_extension_case_insensitively(tmp_path) -> None:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    result = upload_file_to_printer(
        printer_id=9999,
        filename="MODEL.GCODE",
        file_content=_GCODE_CONTENT,
        db_path=db_path,
    )

    assert result is None

def test_upload_printer_not_found_returns_none(tmp_path) -> None:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    result = upload_file_to_printer(
        printer_id=9999,
        filename="test.gcode",
        file_content=_GCODE_CONTENT,
        db_path=db_path,
    )

    assert result is None

    connection = sqlite3.connect(db_path)
    try:
        (job_count,) = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
    finally:
        connection.close()
    assert job_count == 0

def test_upload_happy_path_creates_queued_job_with_metadata(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    result = upload_file_to_printer(
        printer_id=printer_id,
        filename="test.gcode",
        file_content=_GCODE_CONTENT,
        db_path=db_path,
    )

    assert result is not None
    assert result["printer_id"] == printer_id
    assert result["filename"] == "test.gcode"
    assert result["status"] == "queued"
    assert result["file_size_bytes"] == len(_GCODE_CONTENT)
    assert isinstance(result["estimated_print_seconds"], int)
    assert result["estimated_print_seconds"] >= 0

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT status, file_size_bytes, estimated_print_seconds "
            "FROM jobs WHERE id = ?",
            (result["id"],),
        ).fetchone()
    finally:
        connection.close()
    assert row == (
        "queued",
        len(_GCODE_CONTENT),
        result["estimated_print_seconds"],
    )

def test_upload_moonraker_error_raises_and_marks_job_failed(
    client: TestClient, simulator_factory, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(client, handle.host, handle.port)
    printer_id = printer["id"]
    handle.stop()

    with pytest.raises(PrinterCommandError):
        upload_file_to_printer(
            printer_id=printer_id,
            filename="test.gcode",
            file_content=_GCODE_CONTENT,
            db_path=db_path,
        )

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT status FROM jobs WHERE printer_id = ?", (printer_id,)
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("failed",)]
