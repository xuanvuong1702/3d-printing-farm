
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.reports.router as reports_router_module

@pytest.fixture()
def client(reports_db_path: str, monkeypatch) -> TestClient:
    monkeypatch.setattr(reports_router_module, "DEFAULT_DB_PATH", reports_db_path)
    with TestClient(main_module.app) as test_client:
        yield test_client

def test_returns_200_with_no_filter(client, insert_job_history) -> None:
    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T12:00:00Z",
    )

    response = client.get("/reports/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_run_hours"] == 2.0
    assert body["error_rate"] == 0.0
    assert len(body["weekly_production"]) == 1

def test_returns_200_empty_when_filter_matches_no_job(
    client, insert_job_history
) -> None:
    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T12:00:00Z",
    )

    response = client.get(
        "/reports/summary", params={"since": "2026-02-01T00:00:00Z"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_run_hours"] == 0.0
    assert body["error_rate"] is None
    assert body["weekly_production"] == []

def test_returns_404_when_printer_id_not_found(client) -> None:
    response = client.get("/reports/summary", params={"printer_id": 999999})

    assert response.status_code == 404

def test_returns_200_when_printer_id_exists_with_no_job_history(
    client, insert_printer
) -> None:
    printer_id = insert_printer()

    response = client.get("/reports/summary", params={"printer_id": printer_id})

    assert response.status_code == 200
    body = response.json()
    assert body["total_run_hours"] == 0.0
    assert body["error_rate"] is None
    assert body["weekly_production"] == []

@pytest.mark.parametrize("param_name", ["since", "before"])
def test_returns_422_when_since_or_before_has_invalid_format(
    client, param_name: str
) -> None:
    response = client.get("/reports/summary", params={param_name: "not-a-date"})

    assert response.status_code == 422

def test_filters_by_printer_id(client, insert_job_history) -> None:
    printer_a, _ = insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T11:00:00Z",
    )
    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T12:00:00Z",
    )

    response = client.get("/reports/summary", params={"printer_id": printer_a})

    assert response.status_code == 200
    body = response.json()
    assert body["total_run_hours"] == 1.0
    assert len(body["weekly_production"]) == 1
    assert body["weekly_production"][0]["printer_id"] == printer_a
