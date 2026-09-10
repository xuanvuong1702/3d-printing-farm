
from __future__ import annotations

import asyncio
import itertools
import json
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.realtime.router as realtime_router_module
from app.moonraker.http_client import CANONICAL_PRINTING
from app.realtime.service import get_printer_realtime as _real_get_printer_realtime
from app.realtime.service import (
    list_printers_realtime as _real_list_printers_realtime,
)
from app.realtime.service import (
    realtime_sse_event_generator as _real_realtime_sse_event_generator,
)
from app.realtime.state import RealtimePrinterState, RealtimeStateStore

_DB_DEFAULT_STATUS = "UNKNOWN"

def _make_state(progress_percent: int = 42) -> RealtimePrinterState:
    return RealtimePrinterState(
        canonical_status=CANONICAL_PRINTING,
        progress_percent=progress_percent,
        time_remaining_seconds=120,
        filename="test.gcode",
        extruder_temp=210.5,
        extruder_target=210.0,
        bed_temp=60.0,
        bed_target=60.0,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )

@pytest.fixture()
def client(
    realtime_db_path, monkeypatch
) -> Iterator[Tuple[TestClient, RealtimeStateStore]]:

    async def _get_printer_realtime_with_tmp_db(printer_id, store):
        return await _real_get_printer_realtime(
            printer_id, store, db_path=realtime_db_path
        )

    async def _list_printers_realtime_with_tmp_db(store):
        return await _real_list_printers_realtime(store, db_path=realtime_db_path)

    async def _realtime_sse_event_generator_with_tmp_db(
        store, is_disconnected=None
    ) -> AsyncIterator[str]:
        async for line in _real_realtime_sse_event_generator(
            store, is_disconnected=is_disconnected, db_path=realtime_db_path
        ):
            yield line

    monkeypatch.setattr(
        realtime_router_module,
        "get_printer_realtime",
        _get_printer_realtime_with_tmp_db,
    )
    monkeypatch.setattr(
        realtime_router_module,
        "list_printers_realtime",
        _list_printers_realtime_with_tmp_db,
    )
    monkeypatch.setattr(
        realtime_router_module,
        "realtime_sse_event_generator",
        _realtime_sse_event_generator_with_tmp_db,
    )

    with TestClient(main_module.app) as test_client:
        store = RealtimeStateStore()
        main_module.app.state.realtime_store = store
        yield test_client, store

def test_get_printer_realtime_with_data_returns_200(client, insert_printer) -> None:
    test_client, store = client
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy A")
    asyncio.run(store.set(printer_id, _make_state()))

    response = test_client.get(f"/printers/{printer_id}/realtime")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == printer_id
    assert body["name"] == "Máy A"
    assert body["realtime_connected"] is True
    assert body["canonical_status"] == CANONICAL_PRINTING
    assert body["progress_percent"] == 42
    assert body["time_remaining_seconds"] == 120
    assert body["filename"] == "test.gcode"
    assert body["extruder_temp"] == 210.5
    assert body["extruder_target"] == 210.0
    assert body["bed_temp"] == 60.0
    assert body["bed_target"] == 60.0

def test_get_printer_realtime_without_data_falls_back_to_db_status(
    client, insert_printer
) -> None:
    test_client, _store = client
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy B")

    response = test_client.get(f"/printers/{printer_id}/realtime")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == printer_id
    assert body["name"] == "Máy B"
    assert body["realtime_connected"] is False
    assert body["canonical_status"] == _DB_DEFAULT_STATUS
    assert body["progress_percent"] is None
    assert body["time_remaining_seconds"] is None
    assert body["filename"] is None
    assert body["extruder_temp"] is None
    assert body["extruder_target"] is None
    assert body["bed_temp"] is None
    assert body["bed_target"] is None
    assert body["updated_at"] is None

def test_get_printer_realtime_unknown_id_returns_404(client) -> None:
    test_client, _store = client

    response = test_client.get("/printers/999999/realtime")

    assert response.status_code == 404

def test_list_printers_realtime_empty_when_no_printers(client) -> None:
    test_client, _store = client

    response = test_client.get("/printers/realtime")

    assert response.status_code == 200
    assert response.json() == []

def test_list_printers_realtime_mixes_connected_and_not(
    client, insert_printer
) -> None:
    test_client, store = client
    printer_with_data = insert_printer("127.0.0.1", 7125, name="Máy có dữ liệu")
    printer_without_data = insert_printer("127.0.0.2", 7125, name="Máy chưa có dữ liệu")
    asyncio.run(store.set(printer_with_data, _make_state(progress_percent=77)))

    response = test_client.get("/printers/realtime")

    assert response.status_code == 200
    body = {entry["id"]: entry for entry in response.json()}
    assert set(body.keys()) == {printer_with_data, printer_without_data}

    connected = body[printer_with_data]
    assert connected["realtime_connected"] is True
    assert connected["progress_percent"] == 77

    not_connected = body[printer_without_data]
    assert not_connected["realtime_connected"] is False
    assert not_connected["canonical_status"] == _DB_DEFAULT_STATUS
    assert not_connected["progress_percent"] is None

def test_stream_printers_realtime_route_wiring(client) -> None:
    _test_client, store = client

    class _FakeApp:
        def __init__(self, realtime_store: RealtimeStateStore) -> None:
            self.state = type("State", (), {"realtime_store": realtime_store})()

    class _FakeRequest:
        def __init__(self, realtime_store: RealtimeStateStore) -> None:
            self.app = _FakeApp(realtime_store)

        async def is_disconnected(self) -> bool:
            return False

    response = asyncio.run(
        realtime_router_module.stream_printers_realtime(_FakeRequest(store))
    )

    assert response.media_type == "text/event-stream"
    assert response.status_code == 200

def test_realtime_sse_event_generator_reflects_new_state_within_two_intervals(
    realtime_db_path, insert_printer
) -> None:
    interval_seconds = 0.05
    max_wait_seconds = 2 * interval_seconds

    printer_id = insert_printer("127.0.0.1", 7125, name="Máy đo độ trễ")

    async def _scenario() -> float:
        store = RealtimeStateStore()
        generator = _real_realtime_sse_event_generator(
            store, db_path=realtime_db_path, interval_seconds=interval_seconds
        )

        first_line = await generator.__anext__()
        assert '"progress_percent": 99' not in first_line

        start = time.monotonic()
        await store.set(printer_id, _make_state(progress_percent=99))

        async for line in generator:
            if '"progress_percent": 99' in line or '"progress_percent":99' in line:
                elapsed = time.monotonic() - start
                await generator.aclose()
                return elapsed

            if time.monotonic() - start > max_wait_seconds + 5:
                await generator.aclose()
                raise AssertionError("Không nhận được state mới trong thời gian chờ")

        raise AssertionError("Generator kết thúc trước khi phản ánh state mới")

    elapsed_seconds = asyncio.run(_scenario())

    assert elapsed_seconds <= max_wait_seconds

def test_realtime_sse_event_generator_stops_cleanly_on_disconnect(
    realtime_db_path,
) -> None:

    async def _scenario() -> None:
        store = RealtimeStateStore()
        disconnected_flags = itertools.chain([False], itertools.repeat(True))

        async def _is_disconnected() -> bool:
            return next(disconnected_flags)

        generator = _real_realtime_sse_event_generator(
            store,
            is_disconnected=_is_disconnected,
            db_path=realtime_db_path,
            interval_seconds=0.01,
        )

        first_line = await generator.__anext__()
        assert first_line == "data: []\n\n"

        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()

    asyncio.run(_scenario())

def test_realtime_sse_event_generator_emits_valid_sse_format(
    realtime_db_path, insert_printer
) -> None:
    printer_id = insert_printer("127.0.0.1", 7125, name="Máy format SSE")

    async def _scenario() -> str:
        store = RealtimeStateStore()
        await store.set(printer_id, _make_state())
        generator = _real_realtime_sse_event_generator(
            store, db_path=realtime_db_path, interval_seconds=0.01
        )
        try:
            return await generator.__anext__()
        finally:
            await generator.aclose()

    line = asyncio.run(_scenario())

    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    payload = json.loads(line[len("data: ") : -2])
    assert isinstance(payload, list)
    assert payload[0]["id"] == printer_id
    assert payload[0]["progress_percent"] == 42
