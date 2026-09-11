
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.realtime.router as realtime_router_module
from app.realtime.alerts import (
    MAX_LIST_LIMIT,
    _run_alert_watch_cycle,
    list_printer_events,
    record_printer_alert_event,
)
from app.realtime.service import list_printers_realtime as _real_list_printers_realtime
from app.realtime.state import RealtimePrinterState, RealtimeStateStore

def test_record_then_list_returns_the_event(realtime_db_path, insert_printer) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")

    record_printer_alert_event(
        printer_id, "printer_error", "printer_id=%s: IDLE -> ERROR" % printer_id,
        db_path=realtime_db_path,
    )

    events = list_printer_events(db_path=realtime_db_path)

    assert len(events) == 1
    event = events[0]
    assert event.printer_id == printer_id
    assert event.event_type == "printer_error"
    assert event.message == f"printer_id={printer_id}: IDLE -> ERROR"
    assert event.id is not None
    assert event.created_at is not None

def test_list_printer_events_filters_by_printer_id(
    realtime_db_path, insert_printer
) -> None:
    printer_a = insert_printer("127.0.0.1", 7125, name="Máy A")
    printer_b = insert_printer("127.0.0.2", 7125, name="Máy B")
    record_printer_alert_event(
        printer_a, "printer_error", "lỗi máy A", db_path=realtime_db_path
    )
    record_printer_alert_event(
        printer_b, "printer_offline", "mất kết nối máy B", db_path=realtime_db_path
    )

    events_a = list_printer_events(printer_id=printer_a, db_path=realtime_db_path)
    events_b = list_printer_events(printer_id=printer_b, db_path=realtime_db_path)

    assert [e.printer_id for e in events_a] == [printer_a]
    assert [e.printer_id for e in events_b] == [printer_b]

def test_list_printer_events_no_filter_returns_all_printers(
    realtime_db_path, insert_printer
) -> None:
    printer_a = insert_printer("127.0.0.1", 7125, name="Máy A")
    printer_b = insert_printer("127.0.0.2", 7125, name="Máy B")
    record_printer_alert_event(
        printer_a, "printer_error", "lỗi máy A", db_path=realtime_db_path
    )
    record_printer_alert_event(
        printer_b, "printer_offline", "mất kết nối máy B", db_path=realtime_db_path
    )

    events = list_printer_events(db_path=realtime_db_path)

    assert {e.printer_id for e in events} == {printer_a, printer_b}

def test_list_printer_events_respects_limit(realtime_db_path, insert_printer) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    for i in range(5):
        record_printer_alert_event(
            printer_id, "printer_error", f"lỗi lần {i}", db_path=realtime_db_path
        )

    events = list_printer_events(
        printer_id=printer_id, limit=2, db_path=realtime_db_path
    )

    assert len(events) == 2

def test_list_printer_events_limit_capped_at_max_list_limit(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    for i in range(3):
        record_printer_alert_event(
            printer_id, "printer_error", f"lỗi lần {i}", db_path=realtime_db_path
        )

    events = list_printer_events(
        printer_id=printer_id,
        limit=MAX_LIST_LIMIT + 1000,
        db_path=realtime_db_path,
    )

    assert len(events) == 3

def test_list_printer_events_ordered_created_at_desc(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    connection = sqlite3.connect(realtime_db_path)
    try:
        for i, created_at in enumerate(
            ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"]
        ):
            connection.execute(
                "INSERT INTO events (printer_id, job_id, event_type, message, "
                "created_at) VALUES (?, NULL, ?, ?, ?)",
                (printer_id, "printer_error", f"lỗi lần {i}", created_at),
            )
        connection.commit()
    finally:
        connection.close()

    events = list_printer_events(printer_id=printer_id, db_path=realtime_db_path)

    assert [e.created_at for e in events] == [
        "2026-01-03T00:00:00Z",
        "2026-01-02T00:00:00Z",
        "2026-01-01T00:00:00Z",
    ]

def test_record_printer_alert_event_swallows_db_error(tmp_path) -> None:
    invalid_db_path = str(tmp_path / "khong-ton-tai" / "test.db")

    record_printer_alert_event(
        1, "printer_error", "sẽ lỗi khi ghi", db_path=invalid_db_path
    )

def _state(canonical_status: str) -> RealtimePrinterState:
    return RealtimePrinterState(
        canonical_status=canonical_status,
        progress_percent=None,
        time_remaining_seconds=None,
        filename=None,
        extruder_temp=None,
        extruder_target=None,
        bed_temp=None,
        bed_target=None,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )

def test_watch_cycle_first_observation_already_error_logs(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    store = RealtimeStateStore()
    asyncio.run(store.set(printer_id, _state("ERROR")))
    last_seen_status: dict = {}

    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    events = list_printer_events(printer_id=printer_id, db_path=realtime_db_path)
    assert len(events) == 1
    assert events[0].event_type == "printer_error"
    assert last_seen_status[printer_id] == "ERROR"

def test_watch_cycle_stays_same_error_status_does_not_log_again(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    store = RealtimeStateStore()
    asyncio.run(store.set(printer_id, _state("ERROR")))
    last_seen_status: dict = {}

    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    events = list_printer_events(printer_id=printer_id, db_path=realtime_db_path)
    assert len(events) == 1

def test_watch_cycle_leaving_error_status_does_not_log(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    store = RealtimeStateStore()
    last_seen_status: dict = {}

    asyncio.run(store.set(printer_id, _state("ERROR")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    asyncio.run(store.set(printer_id, _state("IDLE")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    events = list_printer_events(printer_id=printer_id, db_path=realtime_db_path)
    assert len(events) == 1
    assert last_seen_status[printer_id] == "IDLE"

def test_watch_cycle_re_entering_error_status_logs_again(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    store = RealtimeStateStore()
    last_seen_status: dict = {}

    asyncio.run(store.set(printer_id, _state("IDLE")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    asyncio.run(store.set(printer_id, _state("ERROR")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    asyncio.run(store.set(printer_id, _state("IDLE")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    asyncio.run(store.set(printer_id, _state("OFFLINE")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    events = list_printer_events(printer_id=printer_id, db_path=realtime_db_path)

    assert sorted(e.event_type for e in events) == ["printer_error", "printer_offline"]

def test_watch_cycle_ignores_non_alert_statuses(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    store = RealtimeStateStore()
    asyncio.run(store.set(printer_id, _state("PRINTING")))
    last_seen_status: dict = {}

    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    events = list_printer_events(printer_id=printer_id, db_path=realtime_db_path)
    assert events == []
    assert last_seen_status[printer_id] == "PRINTING"

def test_watch_cycle_multiple_printers_independent_last_seen_status(
    realtime_db_path, insert_printer
) -> None:
    printer_a = insert_printer("127.0.0.1", 7125, name="Máy A")
    printer_b = insert_printer("127.0.0.2", 7125, name="Máy B")
    store = RealtimeStateStore()
    last_seen_status: dict = {}

    asyncio.run(store.set(printer_a, _state("ERROR")))
    asyncio.run(store.set(printer_b, _state("IDLE")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    asyncio.run(store.set(printer_a, _state("ERROR")))
    asyncio.run(store.set(printer_b, _state("OFFLINE")))
    asyncio.run(_run_alert_watch_cycle(store, last_seen_status, db_path=realtime_db_path))

    events_a = list_printer_events(printer_id=printer_a, db_path=realtime_db_path)
    events_b = list_printer_events(printer_id=printer_b, db_path=realtime_db_path)
    assert len(events_a) == 1
    assert len(events_b) == 1

@pytest.fixture()
def client(realtime_db_path, monkeypatch) -> Iterator[Tuple[TestClient, str]]:

    def _list_printer_events_with_tmp_db(printer_id=None, limit=50):
        return list_printer_events(
            printer_id=printer_id, limit=limit, db_path=realtime_db_path
        )

    async def _list_printers_realtime_with_tmp_db(store):
        return await _real_list_printers_realtime(store, db_path=realtime_db_path)

    monkeypatch.setattr(
        realtime_router_module,
        "list_printer_events",
        _list_printer_events_with_tmp_db,
    )
    monkeypatch.setattr(
        realtime_router_module,
        "list_printers_realtime",
        _list_printers_realtime_with_tmp_db,
    )

    with TestClient(main_module.app) as test_client:
        yield test_client, realtime_db_path

def test_get_printer_events_no_filter_returns_200_with_all_events(
    client, insert_printer
) -> None:
    test_client, db_path = client
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    record_printer_alert_event(
        printer_id, "printer_error", "lỗi máy A", db_path=db_path
    )

    response = test_client.get("/printers/events")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["printer_id"] == printer_id
    assert body[0]["event_type"] == "printer_error"

def test_get_printer_events_filters_by_printer_id(client, insert_printer) -> None:
    test_client, db_path = client
    printer_a = insert_printer("127.0.0.1", 7125, name="Máy A")
    printer_b = insert_printer("127.0.0.2", 7125, name="Máy B")
    record_printer_alert_event(
        printer_a, "printer_error", "lỗi máy A", db_path=db_path
    )
    record_printer_alert_event(
        printer_b, "printer_offline", "mất kết nối máy B", db_path=db_path
    )

    response = test_client.get(f"/printers/events?printer_id={printer_a}")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["printer_id"] == printer_a

def test_get_printer_events_unknown_printer_id_returns_200_empty_list(
    client,
) -> None:
    test_client, _db_path = client

    response = test_client.get("/printers/events?printer_id=999999")

    assert response.status_code == 200
    assert response.json() == []

def test_get_printer_events_respects_limit(client, insert_printer) -> None:
    test_client, db_path = client
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    for i in range(5):
        record_printer_alert_event(
            printer_id, "printer_error", f"lỗi lần {i}", db_path=db_path
        )

    response = test_client.get(f"/printers/events?printer_id={printer_id}&limit=2")

    assert response.status_code == 200
    assert len(response.json()) == 2

def test_get_printer_events_ordered_created_at_desc(client, insert_printer) -> None:
    test_client, db_path = client
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    connection = sqlite3.connect(db_path)
    try:
        for i, created_at in enumerate(
            ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]
        ):
            connection.execute(
                "INSERT INTO events (printer_id, job_id, event_type, message, "
                "created_at) VALUES (?, NULL, ?, ?, ?)",
                (printer_id, "printer_error", f"lỗi lần {i}", created_at),
            )
        connection.commit()
    finally:
        connection.close()

    response = test_client.get(f"/printers/events?printer_id={printer_id}")

    assert response.status_code == 200
    body = response.json()
    assert [entry["created_at"] for entry in body] == [
        "2026-01-02T00:00:00Z",
        "2026-01-01T00:00:00Z",
    ]

def test_existing_routes_still_work_after_alert_watcher_wiring(client) -> None:
    test_client, _db_path = client

    root_response = test_client.get("/")
    realtime_response = test_client.get("/printers/realtime")

    assert root_response.status_code == 200
    assert realtime_response.status_code == 200
