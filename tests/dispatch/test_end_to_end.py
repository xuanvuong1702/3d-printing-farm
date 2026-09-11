
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from app.dispatch.service import run_dispatch_cycle
from app.moonraker.http_client import get_job_queue_status, upload_file

_GCODE_CONTENT = b"G28\nG1 X10 Y10\n"

_NO_JOB_QUEUE_CAPABILITIES = ["klippy_connection", "file_manager"]

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _make_dispatch_branch_b_candidate(db_path: str, printer_id: int) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET capabilities = ?, status = 'IDLE' WHERE id = ?",
            (json.dumps(_NO_JOB_QUEUE_CAPABILITIES), printer_id),
        )
        connection.commit()
    finally:
        connection.close()

def _fetch_job_status_for_printer(db_path: str, printer_id: int) -> str:
    connection = sqlite3.connect(db_path)
    try:
        (status,) = connection.execute(
            "SELECT status FROM jobs WHERE printer_id = ?", (printer_id,)
        ).fetchone()
    finally:
        connection.close()
    return status

def test_both_branches_dispatch_independently_end_to_end(
    client: TestClient, simulator: int, simulator_factory, tmp_path, monkeypatch
) -> None:
    db_path = str(tmp_path / "test_printers.db")

    import app.printers.router as printers_router_module
    from app.printers.service import upload_file_to_printer as _real_upload
    from app.printers.service import (
        enqueue_job_to_moonraker_queue as _real_enqueue,
    )

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

    printer_a = _register_one_printer(client, "127.0.0.1", simulator, "Máy A")
    printer_a_id = printer_a["id"]

    upload_response = client.post(
        f"/printers/{printer_a_id}/files",
        files={"file": ("branch_a.gcode", _GCODE_CONTENT)},
    )
    assert upload_response.status_code == 201

    queue_status_a = get_job_queue_status("127.0.0.1", port=simulator)
    assert queue_status_a["queued_jobs"] == [{"filename": "branch_a.gcode"}]

    handle_b = simulator_factory(host="127.0.0.2")
    printer_b = _register_one_printer(client, handle_b.host, handle_b.port, "Máy B")
    printer_b_id = printer_b["id"]
    _make_dispatch_branch_b_candidate(db_path, printer_b_id)
    upload_file(handle_b.host, "branch_b.gcode", _GCODE_CONTENT, port=handle_b.port)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO jobs (printer_id, filename, status) VALUES (?, ?, ?)",
            (printer_b_id, "branch_b.gcode", "queued"),
        )
        connection.commit()
    finally:
        connection.close()

    run_dispatch_cycle(db_path=db_path)

    assert _fetch_job_status_for_printer(db_path, printer_b_id) == "printing"

    assert _fetch_job_status_for_printer(db_path, printer_a_id) == "queued"
