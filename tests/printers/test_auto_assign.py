
from __future__ import annotations

import asyncio
import sqlite3

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.db.migrate import run_migrations
from app.moonraker.http_client import get_job_queue_status
from app.printers.auto_assign import (
    IdleCandidate,
    NoAvailablePrinterError,
    PrintingCandidate,
    auto_assign_and_upload,
    select_auto_assign_printer,
)
from app.printers.auto_assign import auto_assign_and_upload as _real_auto_assign_and_upload
from app.realtime.state import RealtimePrinterState, RealtimeStateStore

import pytest

_GCODE_CONTENT = b"G28\nG1 X10 Y10\n"

def test_tier1_chon_may_co_queue_ngan_nhat() -> None:
    idle_candidates = [
        IdleCandidate(printer_id=1, queue_length=3),
        IdleCandidate(printer_id=2, queue_length=0),
        IdleCandidate(printer_id=3, queue_length=1),
    ]

    result = select_auto_assign_printer(idle_candidates, [])

    assert result == 2

def test_tier1_tie_break_theo_printer_id_khi_queue_bang_nhau() -> None:
    idle_candidates = [
        IdleCandidate(printer_id=5, queue_length=2),
        IdleCandidate(printer_id=2, queue_length=2),
        IdleCandidate(printer_id=8, queue_length=2),
    ]

    result = select_auto_assign_printer(idle_candidates, [])

    assert result == 2

def test_chi_co_tier2_chon_may_time_remaining_nho_nhat() -> None:
    printing_candidates = [
        PrintingCandidate(printer_id=10, time_remaining_seconds=600),
        PrintingCandidate(printer_id=11, time_remaining_seconds=120),
        PrintingCandidate(printer_id=12, time_remaining_seconds=300),
    ]

    result = select_auto_assign_printer([], printing_candidates)

    assert result == 11

def test_tier2_may_thieu_du_lieu_realtime_xep_cuoi() -> None:
    printing_candidates = [
        PrintingCandidate(printer_id=20, time_remaining_seconds=None),
        PrintingCandidate(printer_id=21, time_remaining_seconds=9999),
    ]

    result = select_auto_assign_printer([], printing_candidates)

    assert result == 21

def test_tier2_toan_bo_thieu_du_lieu_tie_break_theo_id() -> None:
    printing_candidates = [
        PrintingCandidate(printer_id=30, time_remaining_seconds=None),
        PrintingCandidate(printer_id=25, time_remaining_seconds=None),
    ]

    result = select_auto_assign_printer([], printing_candidates)

    assert result == 25

def test_khong_co_ung_vien_nao_tra_ve_none() -> None:
    result = select_auto_assign_printer([], [])

    assert result is None

def test_tier1_luon_thang_tier2_du_tier2_sap_xong_hon() -> None:
    idle_candidates = [IdleCandidate(printer_id=1, queue_length=5)]
    printing_candidates = [
        PrintingCandidate(printer_id=2, time_remaining_seconds=1),
    ]

    result = select_auto_assign_printer(idle_candidates, printing_candidates)

    assert result == 1

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E4-3"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _set_printer_status(db_path: str, printer_id: int, status: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET status = ? WHERE id = ?", (status, printer_id)
        )
        connection.commit()
    finally:
        connection.close()

def test_auto_assign_tier1_happy_path_uploads_and_returns_queued_job(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _set_printer_status(db_path, printer_id, "IDLE")

    store = RealtimeStateStore()

    job = asyncio.run(
        auto_assign_and_upload(
            filename="test.gcode",
            file_content=_GCODE_CONTENT,
            store=store,
            db_path=db_path,
        )
    )

    assert job["printer_id"] == printer_id
    assert job["filename"] == "test.gcode"
    assert job["status"] == "queued"

def test_auto_assign_tier2_only_uploads_to_least_time_remaining(
    client: TestClient, simulator_factory, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    handle_a = simulator_factory(host="127.0.0.1")
    handle_b = simulator_factory(host="127.0.0.2")

    printer_a = _register_one_printer(client, "127.0.0.1", handle_a.port, "A")
    printer_b = _register_one_printer(client, "127.0.0.2", handle_b.port, "B")
    _set_printer_status(db_path, printer_a["id"], "PRINTING")
    _set_printer_status(db_path, printer_b["id"], "PRINTING")

    store = RealtimeStateStore()

    def _state(time_remaining_seconds: int) -> RealtimePrinterState:
        return RealtimePrinterState(
            canonical_status="PRINTING",
            progress_percent=50,
            time_remaining_seconds=time_remaining_seconds,
            filename="other.gcode",
            extruder_temp=None,
            extruder_target=None,
            bed_temp=None,
            bed_target=None,
            updated_at="2026-09-12T00:00:00Z",
        )

    asyncio.run(store.set(printer_a["id"], _state(600)))
    asyncio.run(store.set(printer_b["id"], _state(120)))

    job = asyncio.run(
        auto_assign_and_upload(
            filename="test.gcode",
            file_content=_GCODE_CONTENT,
            store=store,
            db_path=db_path,
        )
    )

    assert job["printer_id"] == printer_b["id"]
    assert job["status"] == "queued"

def test_auto_assign_no_candidates_raises_and_creates_no_job(tmp_path) -> None:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    store = RealtimeStateStore()

    with pytest.raises(NoAvailablePrinterError):
        asyncio.run(
            auto_assign_and_upload(
                filename="test.gcode",
                file_content=_GCODE_CONTENT,
                store=store,
                db_path=db_path,
            )
        )

    connection = sqlite3.connect(db_path)
    try:
        (job_count,) = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
    finally:
        connection.close()
    assert job_count == 0

def test_auto_assign_enqueues_when_job_queue_supported(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    _set_printer_status(db_path, printer["id"], "IDLE")

    store = RealtimeStateStore()

    asyncio.run(
        auto_assign_and_upload(
            filename="test.gcode",
            file_content=_GCODE_CONTENT,
            store=store,
            db_path=db_path,
        )
    )

    queue_status = get_job_queue_status("127.0.0.1", port=simulator)
    assert queue_status["queued_jobs"] == [{"filename": "test.gcode"}]

def _bind_auto_assign_function(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test_printers.db")

    async def _auto_assign_and_upload_with_tmp_db(filename, file_content, store):
        return await _real_auto_assign_and_upload(
            filename=filename,
            file_content=file_content,
            store=store,
            db_path=db_path,
        )

    monkeypatch.setattr(
        printers_router_module,
        "auto_assign_and_upload",
        _auto_assign_and_upload_with_tmp_db,
    )
    return db_path

def test_auto_assign_route_happy_path_returns_201(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_auto_assign_function(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    _set_printer_status(db_path, printer["id"], "IDLE")

    response = client.post(
        "/printers/auto-assign/files",
        files={"file": ("test.gcode", _GCODE_CONTENT)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["printer_id"] == printer["id"]
    assert body["filename"] == "test.gcode"
    assert body["status"] == "queued"

def test_auto_assign_route_no_candidates_returns_409(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    _bind_auto_assign_function(monkeypatch, tmp_path)

    response = client.post(
        "/printers/auto-assign/files",
        files={"file": ("test.gcode", _GCODE_CONTENT)},
    )

    assert response.status_code == 409

def test_auto_assign_route_not_shadowed_by_printer_id_route(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    db_path = _bind_auto_assign_function(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    _set_printer_status(db_path, printer["id"], "IDLE")

    response = client.post(
        "/printers/auto-assign/files",
        files={"file": ("test.gcode", _GCODE_CONTENT)},
    )

    assert response.status_code != 422
    assert response.status_code == 201
    assert response.json()["printer_id"] == printer["id"]
