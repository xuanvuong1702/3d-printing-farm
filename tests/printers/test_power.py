
from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.drivers.base import BaseKlipperDriver
from app.moonraker.http_client import MoonrakerClientError
from app.printers.service import power_off_printer as _real_power_off_printer
from app.printers.service import power_on_printer as _real_power_on_printer

_POWER_DEVICE_NAME = "printer"

def _bind_power(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "power_on_printer",
        lambda printer_id: _real_power_on_printer(printer_id, db_path=db_path),
    )
    monkeypatch.setattr(
        printers_router_module,
        "power_off_printer",
        lambda printer_id: _real_power_off_printer(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-3"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _configure_power_capability(
    db_path: str,
    printer_id: int,
    *,
    supported: bool,
    device_name: Optional[str],
) -> None:
    capabilities: List[str] = ["power"] if supported else []
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET capabilities = ?, power_device_name = ? "
            "WHERE id = ?",
            (json.dumps(capabilities), device_name, printer_id),
        )
        connection.commit()
    finally:
        connection.close()

def test_power_on_happy_path_returns_200(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 200
    body = response.json()
    assert body["power_device_name"] == _POWER_DEVICE_NAME

def test_power_off_happy_path_returns_200(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )

    response = client.post(f"/printers/{printer_id}/power/off")
    assert response.status_code == 200
    body = response.json()
    assert body["power_device_name"] == _POWER_DEVICE_NAME
    assert body["status"]

def test_power_on_not_supported_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(db_path, printer_id, supported=False, device_name=None)

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 409
    assert "detail" in response.json()

def test_power_on_not_configured_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(db_path, printer_id, supported=True, device_name=None)

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 409
    assert "detail" in response.json()

def test_power_on_not_found_returns_404(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    _bind_power(monkeypatch, tmp_path)
    missing_printer_id = 9999

    response = client.post(f"/printers/{missing_printer_id}/power/on")
    assert response.status_code == 404

def test_power_on_moonraker_error_returns_502(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    db_path = _bind_power(monkeypatch, tmp_path)
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(
        client, handle.host, handle.port, name="Printer 502 power_on"
    )
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )
    handle.stop()

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 502
    assert "detail" in response.json()

def test_power_off_get_status_error_after_success_maps_offline(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )

    def _get_status_raises(self) -> None:
        raise MoonrakerClientError("simulated: mất kết nối sau khi cắt điện board")

    monkeypatch.setattr(BaseKlipperDriver, "get_status", _get_status_raises)

    response = client.post(f"/printers/{printer_id}/power/off")
    assert response.status_code == 200
    assert response.json()["status"] == "OFFLINE"
