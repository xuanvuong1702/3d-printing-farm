
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.spools.router as spools_router_module

@pytest.fixture()
def mock_get_spoolman_spool(monkeypatch):

    state = {
        "response": {
            "response": {
                "filament": {"material": "PLA", "name": "Red"},
                "used_weight": 100.0,
                "remaining_weight": 900.0,
            },
            "error": None,
        }
    }

    def _fake_get_spoolman_spool(host, spool_id, port=7125, api_key=None):
        return state["response"]

    monkeypatch.setattr(
        spools_router_module, "get_spoolman_spool", _fake_get_spoolman_spool
    )

    def _set_response(response: dict) -> None:
        state["response"] = response

    return _set_response

@pytest.fixture()
def client(spools_db_path: str, mock_get_spoolman_spool, monkeypatch) -> TestClient:
    monkeypatch.setattr(spools_router_module, "DEFAULT_DB_PATH", spools_db_path)
    with TestClient(main_module.app) as test_client:
        yield test_client

def test_returns_200_with_spool_and_job_data(
    client, insert_job_history
) -> None:
    printer_id, job_history_id = insert_job_history(
        status="finished",
        filename="benchy.gcode",
        start_time="2026-01-05T10:00:00Z",
        spool_id="sp1",
    )

    response = client.get("/spools/sp1", params={"printer_id": printer_id})

    assert response.status_code == 200
    body = response.json()
    assert body["spool_id"] == "sp1"
    assert body["printer_id"] == printer_id
    assert body["material"] == "PLA"
    assert body["filament_name"] == "Red"
    assert body["used_weight_g"] == 100.0
    assert body["remaining_weight_g"] == 900.0
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["job_history_id"] == job_history_id
    assert body["jobs"][0]["filename"] == "benchy.gcode"

def test_returns_404_when_printer_id_not_found(client) -> None:
    response = client.get("/spools/sp1", params={"printer_id": 999999})

    assert response.status_code == 404

def test_returns_404_when_spoolman_reports_spool_not_found(
    client, insert_printer, mock_get_spoolman_spool
) -> None:
    printer_id = insert_printer()
    mock_get_spoolman_spool(
        {"response": None, "error": {"status_code": 404, "message": "Spool not found"}}
    )

    response = client.get("/spools/khong_ton_tai", params={"printer_id": printer_id})

    assert response.status_code == 404

def test_returns_502_when_spoolman_errors_with_other_status(
    client, insert_printer, mock_get_spoolman_spool
) -> None:
    printer_id = insert_printer()
    mock_get_spoolman_spool(
        {
            "response": None,
            "error": {"status_code": 500, "message": "Spoolman not connected"},
        }
    )

    response = client.get("/spools/sp1", params={"printer_id": printer_id})

    assert response.status_code == 502

def test_returns_200_empty_jobs_when_spool_has_no_job_history(
    client, insert_printer
) -> None:
    printer_id = insert_printer()

    response = client.get("/spools/sp1", params={"printer_id": printer_id})

    assert response.status_code == 200
    assert response.json()["jobs"] == []

def test_missing_printer_id_query_param_returns_422(client) -> None:
    response = client.get("/spools/sp1")

    assert response.status_code == 422
