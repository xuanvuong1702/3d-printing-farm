
from __future__ import annotations

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import emergency_stop_printer as _real_emergency_stop_printer

def _bind_emergency_stop(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "emergency_stop_printer",
        lambda printer_id: _real_emergency_stop_printer(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-2"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def test_emergency_stop_happy_path_returns_200_and_offline_status(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    _bind_emergency_stop(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    response = client.post(
        f"/printers/{printer_id}/emergency_stop", json={"confirm": True}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "OFFLINE"

def test_emergency_stop_missing_confirm_returns_422(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    _bind_emergency_stop(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    response = client.post(f"/printers/{printer_id}/emergency_stop", json={})
    assert response.status_code == 422

def test_emergency_stop_confirm_false_returns_422(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    _bind_emergency_stop(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    response = client.post(
        f"/printers/{printer_id}/emergency_stop", json={"confirm": False}
    )
    assert response.status_code == 422

def test_emergency_stop_not_found_returns_404(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    _bind_emergency_stop(monkeypatch, tmp_path)
    missing_printer_id = 9999

    response = client.post(
        f"/printers/{missing_printer_id}/emergency_stop", json={"confirm": True}
    )
    assert response.status_code == 404

def test_emergency_stop_moonraker_error_returns_502(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    _bind_emergency_stop(monkeypatch, tmp_path)
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(
        client, handle.host, handle.port, name="Printer 502 emergency_stop"
    )
    handle.stop()

    response = client.post(
        f"/printers/{printer['id']}/emergency_stop", json={"confirm": True}
    )
    assert response.status_code == 502
    assert "detail" in response.json()
