"""
Test `pytest` chính thức cho `app/realtime/alerts.py` (E2-3/C1+C2) và
route `GET /printers/events` (`app/realtime/router.py`, E2-3/C3) —
chính thức hoá bằng `pytest` toàn bộ hành vi đã kiểm chứng thủ công ở
C1/C2/C3 (xem `docs/CHANGELOG.md` mục "E2-3 / C1", "E2-3 / C2",
"E2-3 / C3"), đúng phạm vi khai báo ở `docs/State_E2-3_v5.md` mục
"CHUNK KẾ TIẾP CẦN CHẠY" — không suy diễn thêm case mới ngoài phạm vi
đã kiểm chứng thủ công. Đây cũng là chunk Integration & Verification
(mục 7 Loop-Controller-Appendix), gộp vào cùng chunk/commit này (đã
khai báo trong chunk plan).

Bố cục theo 3 nhóm, đúng thứ tự (a)/(b)/(c) của state:
- (a) Data layer: `record_printer_alert_event`/`list_printer_events`
  trên DB tạm (`realtime_db_path`, fixture có sẵn của package này).
- (b) Watcher: gọi trực tiếp `_run_alert_watch_cycle` với
  `RealtimeStateStore` thật (không giả lập) qua nhiều vòng — 4 case
  edge-triggered của Quyết định phạm vi #3 (`docs/State_E2-3_v4.md`).
- (c) Wiring route `GET /printers/events` qua `TestClient(app.main.app)`
  — tái dùng đúng kỹ thuật monkeypatch tại nơi tên được bind vào
  `app.realtime.router` mà `tests/realtime/test_router.py::client` đã
  dùng cho 3 route E2-2 (trỏ `list_printer_events` về `realtime_db_path`
  tạm, không sửa `app/realtime/router.py`).

Không có `pytest-asyncio` (xác nhận lại, cùng `tests/realtime/
test_router.py`/`tests/heartbeat/test_scheduler.py`) — nhóm (b) dùng
`asyncio.run(...)` bọc trong hàm test đồng bộ, KHÔNG viết
`async def test_...`.
"""

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
    """Ghi 1 event rồi đọc lại — đúng `printer_id`/`event_type`/
    `message`, `id`/`created_at` do DB tự gán (không truyền tay,
    Quyết định phạm vi #4)."""
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
    """Ghi event cho 2 máy khác nhau — lọc `printer_id` chỉ trả đúng
    máy đó, không lẫn máy kia."""
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
    """`printer_id=None` (mặc định) trả về event của TOÀN BỘ máy."""
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
    """`limit` giới hạn đúng số dòng trả về."""
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
    """`limit` vượt `MAX_LIST_LIMIT` (200) bị chặn trần — không query
    runaway kể cả khi caller (route C3) truyền giá trị lớn hơn (Quyết
    định phạm vi #7). Chèn ít hơn `MAX_LIST_LIMIT` dòng, xin
    `limit=MAX_LIST_LIMIT + 1000` — vẫn chỉ trả đúng số dòng đã chèn
    (không lỗi, không vượt trần), xác nhận việc chặn trần không làm
    hỏng case số dòng thật ít hơn trần."""
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
    """Sắp `created_at DESC` (mới nhất trước) — chèn trực tiếp qua
    `sqlite3` với `created_at` khác nhau rõ ràng (KHÔNG dùng
    `record_printer_alert_event`/`DEFAULT` SQL, vì độ phân giải giây
    của `DEFAULT` có thể trùng giữa các lần insert liên tiếp trong 1
    test - kiểm tra đúng mệnh đề `ORDER BY` của SQL, độc lập với tốc độ
    chèn thật)."""
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
    """Ghi vào `db_path` không hợp lệ (thư mục không tồn tại) — không
    raise (graceful degradation, Quyết định phạm vi #5), cùng kịch bản
    đã kiểm chứng thủ công ở C1."""
    invalid_db_path = str(tmp_path / "khong-ton-tai" / "test.db")

    record_printer_alert_event(
        1, "printer_error", "sẽ lỗi khi ghi", db_path=invalid_db_path
    )

def _state(canonical_status: str) -> RealtimePrinterState:
    """State tối thiểu cho watcher — chỉ `canonical_status` được
    `_run_alert_watch_cycle` đọc tới, các field khác không ảnh hưởng
    kết quả watcher (Quyết định phạm vi #1 — watcher chỉ phụ thuộc
    `RealtimeStateStore`)."""
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
    """Lần đầu quan sát 1 `printer_id` đã ở `ERROR` (`last_seen_status`
    rỗng, `.get()` trả `None`) -> vẫn log (case 1/4)."""
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
    """Đứng yên ở cùng 1 trạng thái lỗi giữa 2 lần poll -> không log
    lặp (case 2/4)."""
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
    """Rời khỏi `ERROR`/`OFFLINE` (về trạng thái không lỗi) -> không
    log ("phục hồi" ngoài phạm vi AC gốc, case 3/4)."""
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
    """Lỗi lại sau khi đã rời khỏi trạng thái lỗi -> log lại (transition
    mới, case 4/4). Đủ 4 vòng: IDLE (chưa lỗi) -> ERROR (log) -> IDLE
    (không log) -> OFFLINE (log lại, transition mới dù khác event_type
    của lần lỗi trước)."""
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
    """Trạng thái không thuộc `{ERROR, OFFLINE}` (ví dụ `PRINTING`)
    không bao giờ log, kể cả lần đầu quan sát."""
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
    """Nhiều máy độc lập — máy này đứng yên ở lỗi không ảnh hưởng tới
    việc phát hiện transition của máy khác trong CÙNG 1 vòng."""
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
    """`TestClient(app.main.app)` cho E2-3/C4 — trỏ `list_printer_events`
    (tên đã bind vào `app.realtime.router` qua `from app.realtime.alerts
    import ... list_printer_events`) về `realtime_db_path` tạm, cùng kỹ
    thuật monkeypatch-tại-nơi-bind mà `tests/realtime/test_router.py::client`
    đã dùng cho 3 route E2-2 — KHÔNG sửa `app/realtime/router.py`.

    `lifespan` thật vẫn chạy (bao gồm `start_alert_watcher`/
    `stop_alert_watcher` mới của C3) nhắm `DEFAULT_DB_PATH` production
    như bình thường — watcher tự dung nạp lỗi đọc DB thiếu bảng thành
    log lỗi rồi bỏ qua (không crash startup), cùng lý do
    `tests/realtime/test_router.py::client` đã nêu cho 2 cơ chế nền
    heartbeat/websocket-pool.

    Cũng monkeypatch `list_printers_realtime` (trỏ về cùng
    `realtime_db_path` tạm) — CHỈ để
    `test_existing_routes_still_work_after_alert_watcher_wiring` gọi
    được `GET /printers/realtime` mà không đụng `DEFAULT_DB_PATH`
    production chưa migrate (cùng kỹ thuật `tests/realtime/
    test_router.py::client` đã dùng), không phải phạm vi cần test lại
    ở chunk này (đã có `tests/realtime/test_router.py` test riêng)."""

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
    """Không filter -> 200, trả đúng event đã ghi."""
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
    """Lọc `printer_id` khớp 1 máy -> chỉ trả event của máy đó."""
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
    """`printer_id` không khớp máy nào trong bảng `events` -> 200 +
    `[]`, KHÔNG 404 (Quyết định phạm vi #7 — khác route snapshot 1 máy
    của E2-2)."""
    test_client, _db_path = client

    response = test_client.get("/printers/events?printer_id=999999")

    assert response.status_code == 200
    assert response.json() == []

def test_get_printer_events_respects_limit(client, insert_printer) -> None:
    """`limit` giới hạn đúng số dòng trả về qua HTTP."""
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
    """Sắp `created_at DESC` qua HTTP — chèn trực tiếp với `created_at`
    khác nhau rõ ràng, cùng lý do đã nêu ở test tương đương của nhóm (a)."""
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
    """2 route cũ (`GET /`, `GET /printers/realtime`) vẫn 200 bình
    thường sau khi `lifespan` đã wiring thêm watcher cảnh báo (C3) —
    xác nhận wiring mới không phá route/`lifespan` hiện có, cùng kịch
    bản đã kiểm chứng thủ công ở C3."""
    test_client, _db_path = client

    root_response = test_client.get("/")
    realtime_response = test_client.get("/printers/realtime")

    assert root_response.status_code == 200
    assert realtime_response.status_code == 200
