
from __future__ import annotations

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import cancel_print as _real_cancel_print
from app.printers.service import pause_print as _real_pause_print
from app.printers.service import resume_print as _real_resume_print
from app.printers.service import start_print as _real_start_print

def _bind_control_functions(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "start_print",
        lambda printer_id, filename, file_content: _real_start_print(
            printer_id, filename, file_content, db_path=db_path
        ),
    )
    monkeypatch.setattr(
        printers_router_module,
        "pause_print",
        lambda printer_id: _real_pause_print(printer_id, db_path=db_path),
    )
    monkeypatch.setattr(
        printers_router_module,
        "resume_print",
        lambda printer_id: _real_resume_print(printer_id, db_path=db_path),
    )
    monkeypatch.setattr(
        printers_router_module,
        "cancel_print",
        lambda printer_id: _real_cancel_print(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-1"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def test_start_pause_resume_cancel_happy_path_updates_status_each_step(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    _bind_control_functions(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    start_response = client.post(
        f"/printers/{printer_id}/print/start",
        files={"file": ("test.gcode", b"G28\nG1 X10 Y10\n")},
    )
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "PRINTING"

    pause_response = client.post(f"/printers/{printer_id}/print/pause")
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "PAUSED"

    resume_response = client.post(f"/printers/{printer_id}/print/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "PRINTING"

    cancel_response = client.post(f"/printers/{printer_id}/print/cancel")
    assert cancel_response.status_code == 200

    assert cancel_response.json()["status"] == "STOPPED"

def test_start_pause_resume_cancel_not_found_returns_404(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    _bind_control_functions(monkeypatch, tmp_path)
    missing_printer_id = 9999

    start_response = client.post(
        f"/printers/{missing_printer_id}/print/start",
        files={"file": ("test.gcode", b"G28\n")},
    )
    assert start_response.status_code == 404

    for action in ("pause", "resume", "cancel"):
        response = client.post(f"/printers/{missing_printer_id}/print/{action}")
        assert response.status_code == 404

def test_start_pause_resume_cancel_moonraker_error_returns_502(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    _bind_control_functions(monkeypatch, tmp_path)

    for index, action in enumerate(("start", "pause", "resume", "cancel")):
        handle = simulator_factory(host=f"127.0.0.{index + 1}")
        printer = _register_one_printer(
            client, handle.host, handle.port, name=f"Printer 502 {action}"
        )
        handle.stop()

        if action == "start":
            response = client.post(
                f"/printers/{printer['id']}/print/start",
                files={"file": ("test.gcode", b"G28\n")},
            )
        else:
            response = client.post(f"/printers/{printer['id']}/print/{action}")

        assert response.status_code == 502
        assert "detail" in response.json()
