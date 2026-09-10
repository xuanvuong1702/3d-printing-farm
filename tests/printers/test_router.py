
from __future__ import annotations

import sqlite3
import time

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import delete_printer as _real_delete_printer
from app.printers.service import list_printers as _real_list_printers
from app.printers.service import update_printer as _real_update_printer

def _count_printers(db_path: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM printers").fetchone()[0]
    finally:
        connection.close()

def _printer_status_by_ip(db_path: str) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT ip, status FROM printers").fetchall()
        return dict(rows)
    finally:
        connection.close()

def _bind_list_printers(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "list_printers",
        lambda: _real_list_printers(db_path=db_path),
    )
    return db_path

def _bind_update_and_delete_printer(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "update_printer",
        lambda printer_id, request: _real_update_printer(
            printer_id, request, db_path=db_path
        ),
    )
    monkeypatch.setattr(
        printers_router_module,
        "delete_printer",
        lambda printer_id: _real_delete_printer(printer_id, db_path=db_path),
    )
    return db_path

def test_register_printer_success(client: TestClient, simulator: int) -> None:
    response = client.post(
        "/printers",
        json={
            "name": "Printer A",
            "ip": "127.0.0.1",
            "moonraker_port": simulator,
            "model": "QIDI Plus4",
            "api_key": "secret-key",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["name"] == "Printer A"
    assert body["ip"] == "127.0.0.1"
    assert body["moonraker_port"] == simulator
    assert body["model"] == "QIDI Plus4"
    assert body["api_key"] == "secret-key"

    assert body["moonraker_version"] == "v0.9.3-simulated"

    assert body["klipper_version"] == "v0.12.0-simulated"
    assert body["capabilities"] == [
        "klippy_connection",
        "file_manager",
        "job_queue",
    ]

    assert body["status"] == "UNKNOWN"
    assert body["is_held"] is False
    assert body["created_at"]
    assert body["updated_at"]

def test_register_printer_optional_fields_omitted(
    client: TestClient, simulator: int
) -> None:
    response = client.post(
        "/printers",
        json={"name": "Printer B", "ip": "127.0.0.1", "moonraker_port": simulator},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["model"] is None
    assert body["api_key"] is None

def test_moonraker_port_defaults_to_7125_when_omitted(
    client: TestClient, simulator_on_default_port: int
) -> None:
    response = client.post(
        "/printers", json={"name": "Printer Default Port", "ip": "127.0.0.1"}
    )

    assert response.status_code == 201
    assert response.json()["moonraker_port"] == 7125 == simulator_on_default_port

def test_register_printer_connection_failure_returns_422_and_does_not_insert(
    client: TestClient, tmp_path
) -> None:
    unused_port = 65000

    response = client.post(
        "/printers",
        json={
            "name": "Printer Unreachable",
            "ip": "127.0.0.1",
            "moonraker_port": unused_port,
        },
    )

    assert response.status_code == 422
    assert "detail" in response.json()

    db_path = str(tmp_path / "test_printers.db")
    assert _count_printers(db_path) == 0

def test_duplicate_ip_returns_409_and_does_not_insert_again(
    client: TestClient, simulator: int, tmp_path
) -> None:
    ip = "127.0.0.1"
    first = client.post(
        "/printers",
        json={"name": "Printer C", "ip": ip, "moonraker_port": simulator},
    )
    assert first.status_code == 201

    second = client.post(
        "/printers",
        json={"name": "Printer C duplicate", "ip": ip, "moonraker_port": simulator},
    )

    assert second.status_code == 409
    assert "detail" in second.json()

    db_path = str(tmp_path / "test_printers.db")
    assert _count_printers(db_path) == 1

def test_root_route_still_works_after_including_printers_router(
    client: TestClient,
) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_list_printers_empty_when_none_registered(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    assert response.json() == []

def test_list_printers_returns_online_status_and_updates_db(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    ip = "127.0.0.1"
    register_response = client.post(
        "/printers",
        json={"name": "Printer Online", "ip": ip, "moonraker_port": simulator},
    )
    assert register_response.status_code == 201

    assert register_response.json()["status"] == "UNKNOWN"

    db_path = _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1

    assert body[0]["status"] == "IDLE"
    assert body[0]["ip"] == ip

    assert _printer_status_by_ip(db_path)[ip] == "IDLE"

def test_list_printers_returns_offline_when_simulator_stopped(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    handle = simulator_factory(host="127.0.0.1")
    ip = handle.host

    register_response = client.post(
        "/printers",
        json={"name": "Printer Offline", "ip": ip, "moonraker_port": handle.port},
    )
    assert register_response.status_code == 201

    handle.stop()

    db_path = _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "OFFLINE"
    assert body[0]["ip"] == ip

    assert _printer_status_by_ip(db_path)[ip] == "OFFLINE"

def test_list_printers_mixed_online_and_offline_printers(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    online_handle = simulator_factory(host="127.0.0.1")
    offline_handle = simulator_factory(host="127.0.0.2")

    online_ip = online_handle.host
    offline_ip = offline_handle.host

    online_register = client.post(
        "/printers",
        json={
            "name": "Printer Mixed Online",
            "ip": online_ip,
            "moonraker_port": online_handle.port,
        },
    )
    assert online_register.status_code == 201

    offline_register = client.post(
        "/printers",
        json={
            "name": "Printer Mixed Offline",
            "ip": offline_ip,
            "moonraker_port": offline_handle.port,
        },
    )
    assert offline_register.status_code == 201

    offline_handle.stop()

    db_path = _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2

    status_by_ip = {item["ip"]: item["status"] for item in body}
    assert status_by_ip[online_ip] == "IDLE"
    assert status_by_ip[offline_ip] == "OFFLINE"

    db_status_by_ip = _printer_status_by_ip(db_path)
    assert db_status_by_ip[online_ip] == "IDLE"
    assert db_status_by_ip[offline_ip] == "OFFLINE"

def _register_one_printer(client: TestClient, simulator: int, name: str = "Printer E1-3") -> dict:
    response = client.post(
        "/printers",
        json={"name": name, "ip": "127.0.0.1", "moonraker_port": simulator},
    )
    assert response.status_code == 201
    return response.json()

def test_patch_printer_updates_name(client: TestClient, simulator: int, tmp_path, monkeypatch) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    time.sleep(1.1)

    response = client.patch(f"/printers/{registered['id']}", json={"name": "Printer Renamed"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Printer Renamed"
    assert body["updated_at"] != registered["updated_at"]

    assert body["ip"] == registered["ip"]

def test_patch_printer_updates_model_and_api_key_including_explicit_null(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    response = client.post(
        "/printers",
        json={
            "name": "Printer With Model",
            "ip": "127.0.0.1",
            "moonraker_port": simulator,
            "model": "QIDI Plus4",
            "api_key": "secret-key",
        },
    )
    assert response.status_code == 201
    printer_id = response.json()["id"]

    patch_response = client.patch(
        f"/printers/{printer_id}",
        json={"model": "QIDI X-Max3", "api_key": None},
    )

    assert patch_response.status_code == 200
    body = patch_response.json()
    assert body["model"] == "QIDI X-Max3"
    assert body["api_key"] is None

def test_patch_printer_empty_body_is_noop_and_keeps_updated_at(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    first_patch = client.patch(f"/printers/{registered['id']}", json={"name": "Printer First Patch"})
    assert first_patch.status_code == 200
    updated_at_after_real_patch = first_patch.json()["updated_at"]

    empty_patch = client.patch(f"/printers/{registered['id']}", json={})

    assert empty_patch.status_code == 200
    body = empty_patch.json()
    assert body["name"] == "Printer First Patch"
    assert body["updated_at"] == updated_at_after_real_patch

def test_patch_printer_not_found_returns_404(client: TestClient, tmp_path, monkeypatch) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)

    response = client.patch("/printers/999999", json={"name": "Ghost Printer"})

    assert response.status_code == 404
    assert "detail" in response.json()

def test_patch_printer_ignores_ip_field(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    response = client.patch(
        f"/printers/{registered['id']}",
        json={"name": "Printer IP Unchanged", "ip": "10.0.0.99"},
    )

    assert response.status_code == 200
    assert response.json()["ip"] == registered["ip"]

def test_delete_printer_success_returns_204_and_removes_row(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)
    assert _count_printers(db_path) == 1

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert _count_printers(db_path) == 0

def test_delete_printer_not_found_returns_404(client: TestClient, tmp_path, monkeypatch) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)

    response = client.delete("/printers/999999")

    assert response.status_code == 404
    assert "detail" in response.json()

def _set_printer_status(db_path: str, printer_id: int, status_value: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET status = ? WHERE id = ?", (status_value, printer_id)
        )
        connection.commit()
    finally:
        connection.close()

def test_delete_printer_blocked_when_status_is_printing(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)
    _set_printer_status(db_path, registered["id"], "PRINTING")

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 409
    assert "detail" in response.json()
    assert _count_printers(db_path) == 1

def test_delete_printer_blocked_when_status_is_paused(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)
    _set_printer_status(db_path, registered["id"], "PAUSED")

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 409
    assert _count_printers(db_path) == 1

def test_delete_printer_blocked_by_related_job_row(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO jobs (printer_id, filename) VALUES (?, ?)",
            (registered["id"], "test-model.gcode"),
        )
        connection.commit()
    finally:
        connection.close()

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 409
    assert "detail" in response.json()
    assert _count_printers(db_path) == 1
