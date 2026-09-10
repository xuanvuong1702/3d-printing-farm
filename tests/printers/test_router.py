
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

def _count_printers(db_path: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM printers").fetchone()[0]
    finally:
        connection.close()

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
