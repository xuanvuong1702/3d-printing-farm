
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import confirm_printer as _real_confirm_printer

_STATUS_BEFORE_CONFIRM = "FINISHED"

def _bind_confirm(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "confirm_printer",
        lambda printer_id: _real_confirm_printer(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-4"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _set_is_held(db_path: str, printer_id: int, *, is_held: bool, status: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET is_held = ?, status = ? WHERE id = ?",
            (1 if is_held else 0, status, printer_id),
        )
        connection.commit()
    finally:
        connection.close()

def test_confirm_happy_path_returns_200_and_clears_is_held(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_confirm(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _set_is_held(db_path, printer_id, is_held=True, status=_STATUS_BEFORE_CONFIRM)

    response = client.post(f"/printers/{printer_id}/confirm")
    assert response.status_code == 200
    body = response.json()
    assert body["is_held"] is False
    assert body["status"] == _STATUS_BEFORE_CONFIRM

def test_confirm_not_held_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_confirm(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _set_is_held(db_path, printer_id, is_held=False, status="IDLE")

    response = client.post(f"/printers/{printer_id}/confirm")
    assert response.status_code == 409
    assert "detail" in response.json()

def test_confirm_not_found_returns_404(client: TestClient, tmp_path, monkeypatch) -> None:
    _bind_confirm(monkeypatch, tmp_path)
    missing_printer_id = 9999

    response = client.post(f"/printers/{missing_printer_id}/confirm")
    assert response.status_code == 404

def test_confirm_twice_second_call_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_confirm(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _set_is_held(db_path, printer_id, is_held=True, status=_STATUS_BEFORE_CONFIRM)

    first_response = client.post(f"/printers/{printer_id}/confirm")
    assert first_response.status_code == 200

    second_response = client.post(f"/printers/{printer_id}/confirm")
    assert second_response.status_code == 409
    assert "detail" in second_response.json()
