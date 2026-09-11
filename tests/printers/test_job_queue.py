
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db.migrate import run_migrations
from app.moonraker.http_client import get_job_queue_status
from app.printers.service import (
    PrinterCommandError,
    enqueue_job_to_moonraker_queue,
)

_GCODE_CONTENT = b"G28\nG1 X10 Y10\n"

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E4-2"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _clear_job_queue_capability(db_path: str, printer_id: int) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET capabilities = ? WHERE id = ?",
            (json.dumps(["klippy_connection", "file_manager"]), printer_id),
        )
        connection.commit()
    finally:
        connection.close()

def test_enqueue_printer_not_found_returns_none(tmp_path) -> None:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    result = enqueue_job_to_moonraker_queue(
        printer_id=9999, filename="test.gcode", db_path=db_path
    )

    assert result is None

def test_enqueue_skips_when_no_job_queue_capability(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _clear_job_queue_capability(db_path, printer_id)

    result = enqueue_job_to_moonraker_queue(
        printer_id=printer_id, filename="test.gcode", db_path=db_path
    )

    assert result is False

def test_enqueue_happy_path_reflects_in_moonraker_job_queue_status(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    result = enqueue_job_to_moonraker_queue(
        printer_id=printer_id, filename="test.gcode", db_path=db_path
    )

    assert result is True

    queue_status = get_job_queue_status("127.0.0.1", port=simulator)
    assert queue_status["queued_jobs"] == [{"filename": "test.gcode"}]

def test_enqueue_moonraker_error_raises_command_error(
    client: TestClient, simulator_factory, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(client, handle.host, handle.port)
    printer_id = printer["id"]
    handle.stop()

    with pytest.raises(PrinterCommandError):
        enqueue_job_to_moonraker_queue(
            printer_id=printer_id, filename="test.gcode", db_path=db_path
        )

def test_upload_route_auto_enqueues_when_job_queue_supported(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    import app.printers.router as printers_router_module
    from app.printers.service import upload_file_to_printer as _real_upload
    from app.printers.service import (
        enqueue_job_to_moonraker_queue as _real_enqueue,
    )

    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "upload_file_to_printer",
        lambda printer_id, filename, file_content: _real_upload(
            printer_id, filename, file_content, db_path=db_path
        ),
    )
    monkeypatch.setattr(
        printers_router_module,
        "enqueue_job_to_moonraker_queue",
        lambda printer_id, filename: _real_enqueue(
            printer_id, filename, db_path=db_path
        ),
    )

    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    response = client.post(
        f"/printers/{printer_id}/files",
        files={"file": ("test.gcode", _GCODE_CONTENT)},
    )

    assert response.status_code == 201

    queue_status = get_job_queue_status("127.0.0.1", port=simulator)
    assert queue_status["queued_jobs"] == [{"filename": "test.gcode"}]
