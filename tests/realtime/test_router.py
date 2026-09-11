"""
Test `pytest` chính thức cho `app/realtime/router.py` (E2-2/C4) — 3
endpoint đã viết ở C2/C3 (`docs/State_E2-2_v5.md` mục "CHUNK KẾ TIẾP
CẦN CHẠY"):
- `GET /printers/{printer_id}/realtime` — snapshot 1 máy.
- `GET /printers/realtime` — snapshot toàn bộ máy.
- `GET /printers/realtime/stream` — SSE, đẩy định kỳ mỗi
  `REALTIME_STREAM_INTERVAL_SECONDS`.

Không có `pytest-asyncio` (xác nhận lại, cùng `tests/realtime/
test_pool.py`) — test độ trễ/dừng sạch của SSE gọi THẲNG
`realtime_sse_event_generator` (`app/realtime/service.py`) ở tầng
unit, bọc trong `asyncio.run(...)`, KHÔNG qua `TestClient`/
`httpx.AsyncClient` streaming đầy đủ vòng đời — đã quan sát hiện tượng
treo do `httpx.ASGITransport` không mô phỏng đầy đủ tín hiệu
disconnect ở tầng transport lúc kiểm chứng thủ công C3 (xem
`docs/CHANGELOG.md` mục "E2-2 / C3"). `TestClient` (đồng bộ, KHÔNG đọc
hết body streaming — dùng `client.stream(...)` rồi đóng ngay) chỉ dùng
để xác nhận route wiring (status/media_type) của endpoint stream.
"""

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
    """State giả lập 1 notification WS vừa tới — AC "≤2s" đo độ trễ
    tầng API-tới-client, KHÔNG phải độ trễ Moonraker-tới-service (đã đo
    ở E2-1), nên không cần simulator WS thật (Quyết định phạm vi #5,
    `docs/State_E2-2_v5.md`)."""
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
    """`TestClient(app.main.app)` cho E2-2/C4 — trỏ 3 hàm mà
    `app.realtime.router` đã import (`get_printer_realtime`/
    `list_printers_realtime`/`realtime_sse_event_generator`) về
    `realtime_db_path` tạm (kỹ thuật monkeypatch tại nơi tên được
    bind, cùng cách `tests/printers/conftest.py::client` đã dùng cho
    `register_printer`) — KHÔNG sửa `app/realtime/router.py`/
    `service.py`.

    `app.state.realtime_store` được override bằng 1
    `RealtimeStateStore` mới do test tự kiểm soát NGAY SAU khi
    `lifespan` khởi động xong — không phụ thuộc `lifespan` thật kết
    nối Moonraker khi test (đúng yêu cầu ở `docs/State_E2-2_v5.md`
    mục "CHUNK KẾ TIẾP CẦN CHẠY"). `lifespan` vẫn tự chạy
    `start_websocket_pool()`/`start_heartbeat_scheduler()` nhắm
    `DEFAULT_DB_PATH` production như bình thường (KHÔNG monkeypatch 2
    cơ chế nền đó — khác `tests/printers/conftest.py::client`) vì cả
    hai đã tự dung nạp lỗi đọc DB thiếu bảng/không tồn tại thành "0
    máy"/log lỗi rồi bỏ qua (E1-4/C3, E2-1/C4 — xem docstring
    `WebsocketPool.start()`), không crash startup; route real-time chỉ
    đọc qua `request.app.state.realtime_store`, không đụng gì tới 2 cơ
    chế nền đó nên không cần override.
    """

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
    """Máy đã có dữ liệu realtime (set trước qua `store.set()`) -> 200,
    đúng field, `realtime_connected=True`."""
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
    """Máy tồn tại trong DB nhưng CHƯA có dữ liệu realtime (Quyết định
    phạm vi #4) -> 200, `realtime_connected=False`, `canonical_status`
    fallback về `printers.status` DB, các field còn lại `null`."""
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
    """`printer_id` không tồn tại trong bảng `printers` -> 404."""
    test_client, _store = client

    response = test_client.get("/printers/999999/realtime")

    assert response.status_code == 404

def test_list_printers_realtime_empty_when_no_printers(client) -> None:
    """Danh sách rỗng khi chưa có máy nào đăng ký — không phải lỗi."""
    test_client, _store = client

    response = test_client.get("/printers/realtime")

    assert response.status_code == 200
    assert response.json() == []

def test_list_printers_realtime_mixes_connected_and_not(
    client, insert_printer
) -> None:
    """Nhiều máy — trộn cả 2 case có/chưa dữ liệu realtime, đúng cho
    từng máy theo `printer_id`."""
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
    """Xác nhận wiring đúng (status mặc định 200, `media_type=
    text/event-stream`, đọc đúng `request.app.state.realtime_store`) —
    gọi THẲNG hàm route `stream_printers_realtime` với 1 `Request` giả
    tối thiểu (duck-typing `app.state`/`is_disconnected`), KHÔNG gửi
    qua `TestClient`/ASGI transport thật và KHÔNG lặp qua
    `response.body_iterator` — tránh hiện tượng treo do
    `httpx.ASGITransport` không mô phỏng đầy đủ tín hiệu disconnect ở
    tầng transport (đã quan sát khi kiểm chứng thủ công C3 và lại vừa
    tái hiện lúc viết chunk C4 này, xem docstring module)."""
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
    """Đo độ trễ AC "≤2s": ghi 1 `RealtimePrinterState` mới vào store
    NGAY SAU dòng SSE đầu tiên (giả lập notification WS vừa tới), xác
    nhận dòng SSE tiếp theo phản ánh đúng giá trị mới trong ≤ 2 lần
    `interval_seconds` — trường hợp xấu nhất (vừa bỏ lỡ 1 tick ngay
    trước khi state đổi), đúng Quyết định phạm vi #5
    (`docs/State_E2-2_v5.md`). Dùng `interval_seconds` nhỏ (0.05s) để
    test chạy nhanh, cùng tỉ lệ với hằng số production thật
    (`REALTIME_STREAM_INTERVAL_SECONDS`), test trực tiếp
    `realtime_sse_event_generator` ở tầng unit (không qua HTTP) theo
    hướng dẫn của `docs/State_E2-2_v5.md` khi streaming HTTP thật
    không ổn định."""
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
    """`is_disconnected() -> True` khiến generator dừng sạch
    (`StopAsyncIteration`), không rò rỉ vòng lặp — kiểm chứng lại
    chính thức bằng `pytest` hành vi đã kiểm chứng thủ công ở C3 (xem
    `docs/CHANGELOG.md` mục "E2-2 / C3")."""

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
    """Xác nhận format 1 dòng SSE hợp lệ (`"data: " + JSON + "\\n\\n"`)
    và JSON đúng cấu trúc `PrinterRealtimeResponse` (list)."""
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
