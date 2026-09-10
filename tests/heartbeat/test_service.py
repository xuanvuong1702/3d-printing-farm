"""
Test cho `run_heartbeat_cycle` (E1-4/C2, `app/heartbeat/service.py`) —
chính thức hoá lại các kịch bản đã kiểm chứng tạm ở C2 (script pytest độc
lập, đã xoá sau khi xác nhận — xem `docs/Story_E1-4.md` mục "Cách chạy/
kiểm chứng" chunk C2), viết lại theo chuẩn pytest thật, KHÔNG copy y
nguyên script tạm.

Dùng `simulator_factory`/`heartbeat_db_path`/`insert_printer`/
`fetch_printer`/`set_next_heartbeat_at` (xem `conftest.py` của package
này) — KHÔNG dùng fixture `client`: `run_heartbeat_cycle` là hàm THUẦN,
không cần `app.main.app`; dùng `client` sẽ khởi động scheduler nền thật
qua `lifespan` (C3), có thể ghi đè cùng lúc test đang tự gọi
`run_heartbeat_cycle` trực tiếp trên cùng DB -> kết quả không xác định
(xem rationale đầy đủ ở docstring module của `conftest.py`).

Ghi chú về công thức backoff (Quyết định 5, `docs/State_E1-4_v5.md`) áp
dụng ĐÚNG cho cả LẦN LỖI ĐẦU TIÊN (không có nhánh đặc biệt "lần đầu dùng
interval mặc định 30s chưa nhân hệ số" trong code đã khoá ở C2,
`app/heartbeat/service.py::run_heartbeat_cycle`):
`interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS *
(DEFAULT_BACKOFF_MULTIPLIER ** consecutive_heartbeat_failures)` với
`consecutive_heartbeat_failures` là giá trị SAU KHI tăng (tức đã +1) -
lần lỗi đầu tiên (n=1) cho `30.0 * 2.0**1 = 60.0s`, KHÔNG phải
`30.0s`. Các test dưới đây assert đúng giá trị numeric này (không giả
định "chưa backoff" một cách lỏng lẻo), và giá trị này nhất quán với
điểm dữ liệu đã xác nhận ở kịch bản tạm C2 cho lần lỗi thứ 2
(`30.0 * 2.0**2 = 120.0s`, xem `docs/Story_E1-4.md`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.heartbeat.service import (
    DEFAULT_BACKOFF_MULTIPLIER,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    run_heartbeat_cycle,
)

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_TOLERANCE_SECONDS = 5.0

_FAR_FUTURE_ISO = "2099-01-01T00:00:00Z"

def _parse(value: str) -> datetime:
    return datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=timezone.utc)

def _assert_close_to_now_plus(value: str, seconds: float) -> None:
    expected = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    actual = _parse(value)
    delta = abs((actual - expected).total_seconds())
    assert delta <= _TOLERANCE_SECONDS, (
        f"{value!r} lệch {delta:.1f}s so với kỳ vọng "
        f"{expected.isoformat()} (dung sai {_TOLERANCE_SECONDS}s)"
    )

def test_online_printer_updates_status_resets_failures_and_sets_next_heartbeat(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer
):
    """Máy online (simulator sống) -> status = canonical thật (simulator
    mặc định print_stats.state="standby" -> canonical "IDLE", cùng giá
    trị đã xác nhận ở `tests/printers/test_router.py::
    test_list_printers_returns_online_status_and_updates_db`),
    `consecutive_heartbeat_failures` reset về 0, `next_heartbeat_at` = now
    + interval mặc định (không backoff khi thành công, Quyết định 5)."""
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port)

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "IDLE"
    assert failures == 0
    _assert_close_to_now_plus(next_heartbeat_at, DEFAULT_HEARTBEAT_INTERVAL_SECONDS)

def test_offline_printer_marks_offline_and_increments_failures_first_time(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer
):
    """Máy offline (simulator tắt trước khi heartbeat chạy, qua
    `simulator_factory().stop()`) -> status = OFFLINE (Quyết định 7,
    D-013), consecutive_heartbeat_failures tăng đúng 1 (0 -> 1),
    next_heartbeat_at theo công thức backoff với n=1 (`30.0 * 2.0**1 =
    60.0s` - xem ghi chú ở đầu file, KHÔNG phải interval mặc định
    30.0s)."""
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port)
    handle.stop()

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "OFFLINE"
    assert failures == 1
    expected_interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS * (DEFAULT_BACKOFF_MULTIPLIER**1)
    assert expected_interval == 60.0
    _assert_close_to_now_plus(next_heartbeat_at, expected_interval)

def test_printer_not_due_yet_is_skipped(
    heartbeat_db_path, insert_printer, fetch_printer
):
    """Máy CHƯA tới lượt (`next_heartbeat_at` còn ở tương lai xa) -> bị
    loại khỏi `_SELECT_DUE_PRINTERS_SQL` (`WHERE next_heartbeat_at IS
    NULL OR next_heartbeat_at <= now`), không đổi bất kỳ field nào. Không
    cần simulator nào chạy - máy này không bao giờ được gọi tới."""
    printer_id = insert_printer(
        "203.0.113.1",
        7125,
        consecutive_heartbeat_failures=3,
        next_heartbeat_at=_FAR_FUTURE_ISO,
    )

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "UNKNOWN"
    assert failures == 3
    assert next_heartbeat_at == _FAR_FUTURE_ISO

def test_consecutive_failures_apply_exponential_backoff(
    simulator_factory,
    heartbeat_db_path,
    insert_printer,
    fetch_printer,
    set_next_heartbeat_at,
):
    """Lỗi liên tiếp 2 lần (ép `next_heartbeat_at` về quá khứ giữa 2 lần
    gọi `run_heartbeat_cycle`) -> backoff tăng đúng công thức cấp số
    nhân: lần lỗi thứ 2 (n=2) cho `30.0 * 2.0**2 = 120.0s` - cùng điểm dữ
    liệu numeric đã xác nhận ở kịch bản tạm C2 (`docs/Story_E1-4.md`)."""
    handle = simulator_factory(host="127.0.0.1")
    printer_id = insert_printer(handle.host, handle.port)
    handle.stop()

    run_heartbeat_cycle(db_path=heartbeat_db_path)
    _, failures_after_first, _ = fetch_printer(printer_id)
    assert failures_after_first == 1

    set_next_heartbeat_at(printer_id, "2000-01-01T00:00:00Z")

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    status, failures, next_heartbeat_at = fetch_printer(printer_id)
    assert status == "OFFLINE"
    assert failures == 2
    expected_interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS * (DEFAULT_BACKOFF_MULTIPLIER**2)
    assert expected_interval == 120.0
    _assert_close_to_now_plus(next_heartbeat_at, expected_interval)

def test_mixed_online_and_offline_printers_in_one_cycle(
    simulator_factory, heartbeat_db_path, insert_printer, fetch_printer
):
    """Trộn 1 máy online + 1 máy offline trong cùng 1 lần gọi
    `run_heartbeat_cycle` -> máy lỗi không làm hỏng kết quả của máy kia
    (đối chiếu D-013/pattern `list_printers`, 2 địa chỉ loopback khác
    nhau vì cột `ip` có `UNIQUE` constraint, không phải `(ip, port)`)."""
    online_handle = simulator_factory(host="127.0.0.1")
    offline_handle = simulator_factory(host="127.0.0.2")
    offline_handle.stop()

    online_id = insert_printer(online_handle.host, online_handle.port)
    offline_id = insert_printer(offline_handle.host, offline_handle.port)

    run_heartbeat_cycle(db_path=heartbeat_db_path)

    online_status, online_failures, online_next = fetch_printer(online_id)
    assert online_status == "IDLE"
    assert online_failures == 0
    _assert_close_to_now_plus(online_next, DEFAULT_HEARTBEAT_INTERVAL_SECONDS)

    offline_status, offline_failures, _ = fetch_printer(offline_id)
    assert offline_status == "OFFLINE"
    assert offline_failures == 1

def test_run_heartbeat_cycle_on_empty_table_does_not_raise(heartbeat_db_path):
    """Không có máy nào đăng ký (tương đương edge case "máy bị xoá giữa
    chừng" đã kiểm chứng tạm ở C3 - SELECT 0 dòng) -> chạy êm, không
    raise (`docs/Story_E1-4.md` mục "Cách chạy/kiểm chứng" chunk C3)."""
    run_heartbeat_cycle(db_path=heartbeat_db_path)
