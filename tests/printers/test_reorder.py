
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db.migrate import run_migrations
from app.moonraker.http_client import get_job_queue_status
from app.printers.service import (
    InvalidReorderError,
    PrinterCommandError,
    reorder_printer_queue,
)

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E4-4"
) -> dict:
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _clear_job_queue_capability(db_path: str, printer_id: int) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET capabilities = ? WHERE id = ?",
            (json.dumps(["klippy_connection", "file_manager"]), printer_id),
        )
        connection.commit()
    finally:
        connection.close()

def _insert_queued_job(db_path: str, printer_id: int, filename: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            "INSERT INTO jobs (printer_id, filename, status) VALUES (?, ?, 'queued')",
            (printer_id, filename),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()

def _insert_job_with_status(
    db_path: str, printer_id: int, filename: str, status: str
) -> int:
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            "INSERT INTO jobs (printer_id, filename, status) VALUES (?, ?, ?)",
            (printer_id, filename, status),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()

def _read_priorities(db_path: str, job_ids: list[int]) -> dict[int, int]:
    connection = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in job_ids)
        rows = connection.execute(
            f"SELECT id, priority FROM jobs WHERE id IN ({placeholders})",
            job_ids,
        ).fetchall()
        return dict(rows)
    finally:
        connection.close()

def test_reorder_printer_not_found_returns_none(tmp_path) -> None:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    result = reorder_printer_queue(printer_id=9999, job_ids=[], db_path=db_path)

    assert result is None

def test_reorder_rejects_missing_job_id(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")
    _insert_queued_job(db_path, printer_id, "b.gcode")

    with pytest.raises(InvalidReorderError):
        reorder_printer_queue(printer_id, [job_1], db_path=db_path)

def test_reorder_rejects_unknown_job_id(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")

    with pytest.raises(InvalidReorderError):
        reorder_printer_queue(printer_id, [job_1, 99999], db_path=db_path)

def test_reorder_rejects_duplicate_job_id(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")
    job_2 = _insert_queued_job(db_path, printer_id, "b.gcode")

    with pytest.raises(InvalidReorderError):
        reorder_printer_queue(printer_id, [job_1, job_1], db_path=db_path)

    assert job_2 is not None

def test_reorder_rejects_job_from_another_printer(
    client: TestClient, simulator_factory, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    handle_a = simulator_factory(host="127.0.0.1")
    handle_b = simulator_factory(host="127.0.0.2")
    printer_a = _register_one_printer(client, handle_a.host, handle_a.port, "A")
    printer_b = _register_one_printer(client, handle_b.host, handle_b.port, "B")

    job_a = _insert_queued_job(db_path, printer_a["id"], "a.gcode")
    job_b = _insert_queued_job(db_path, printer_b["id"], "b.gcode")

    with pytest.raises(InvalidReorderError):
        reorder_printer_queue(printer_a["id"], [job_a, job_b], db_path=db_path)

def test_reorder_rejects_non_queued_job(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    job_queued = _insert_queued_job(db_path, printer_id, "a.gcode")
    job_printing = _insert_job_with_status(
        db_path, printer_id, "b.gcode", "printing"
    )

    with pytest.raises(InvalidReorderError):
        reorder_printer_queue(
            printer_id, [job_queued, job_printing], db_path=db_path
        )

def test_reorder_invalid_permutation_does_not_write_priority(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _clear_job_queue_capability(db_path, printer_id)
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")
    job_2 = _insert_queued_job(db_path, printer_id, "b.gcode")

    with pytest.raises(InvalidReorderError):
        reorder_printer_queue(printer_id, [job_1], db_path=db_path)

    priorities = _read_priorities(db_path, [job_1, job_2])
    assert priorities == {job_1: 0, job_2: 0}

def test_reorder_branch_b_updates_priority_in_new_order(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _clear_job_queue_capability(db_path, printer_id)
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")
    job_2 = _insert_queued_job(db_path, printer_id, "b.gcode")
    job_3 = _insert_queued_job(db_path, printer_id, "c.gcode")

    new_order = [job_3, job_1, job_2]
    result = reorder_printer_queue(printer_id, new_order, db_path=db_path)

    assert [job["id"] for job in result] == new_order
    priorities = _read_priorities(db_path, new_order)
    assert priorities == {job_3: 3, job_1: 2, job_2: 1}

def test_reorder_branch_a_reflects_new_order_in_moonraker_job_queue(
    client: TestClient, simulator: int, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")
    job_2 = _insert_queued_job(db_path, printer_id, "b.gcode")
    job_3 = _insert_queued_job(db_path, printer_id, "c.gcode")

    new_order = [job_2, job_3, job_1]
    result = reorder_printer_queue(printer_id, new_order, db_path=db_path)

    assert [job["id"] for job in result] == new_order

    queue_status = get_job_queue_status("127.0.0.1", port=simulator)
    assert queue_status["queued_jobs"] == [
        {"filename": "b.gcode"},
        {"filename": "c.gcode"},
        {"filename": "a.gcode"},
    ]

def test_reorder_branch_a_moonraker_error_raises_command_error(
    client: TestClient, simulator_factory, tmp_path
) -> None:
    db_path = str(tmp_path / "test_printers.db")
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(client, handle.host, handle.port)
    printer_id = printer["id"]
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")
    job_2 = _insert_queued_job(db_path, printer_id, "b.gcode")
    handle.stop()

    with pytest.raises(PrinterCommandError):
        reorder_printer_queue(printer_id, [job_2, job_1], db_path=db_path)

def test_reorder_route_happy_path_returns_200_in_new_order(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    import app.printers.router as printers_router_module
    from app.printers.service import reorder_printer_queue as _real_reorder

    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "reorder_printer_queue",
        lambda printer_id, job_ids: _real_reorder(
            printer_id, job_ids, db_path=db_path
        ),
    )

    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    job_1 = _insert_queued_job(db_path, printer_id, "a.gcode")
    job_2 = _insert_queued_job(db_path, printer_id, "b.gcode")

    response = client.post(
        f"/printers/{printer_id}/queue/reorder",
        json={"job_ids": [job_2, job_1]},
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [job_2, job_1]

def test_reorder_route_invalid_permutation_returns_422(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    import app.printers.router as printers_router_module
    from app.printers.service import reorder_printer_queue as _real_reorder

    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "reorder_printer_queue",
        lambda printer_id, job_ids: _real_reorder(
            printer_id, job_ids, db_path=db_path
        ),
    )

    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _insert_queued_job(db_path, printer_id, "a.gcode")
    _insert_queued_job(db_path, printer_id, "b.gcode")

    response = client.post(
        f"/printers/{printer_id}/queue/reorder",
        json={"job_ids": [99999]},
    )

    assert response.status_code == 422

def test_reorder_route_not_found_returns_404(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    import app.printers.router as printers_router_module
    from app.printers.service import reorder_printer_queue as _real_reorder

    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)
    monkeypatch.setattr(
        printers_router_module,
        "reorder_printer_queue",
        lambda printer_id, job_ids: _real_reorder(
            printer_id, job_ids, db_path=db_path
        ),
    )

    response = client.post(
        "/printers/9999/queue/reorder",
        json={"job_ids": []},
    )

    assert response.status_code == 404
