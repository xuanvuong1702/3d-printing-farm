"""
Test cho `app/heartbeat/scheduler.py` (E1-4/C3) — chính thức hoá lại 2
kịch bản đã kiểm chứng tạm ở C3 (xem `docs/Story_E1-4.md` mục "Cách
chạy/kiểm chứng" chunk C3): (1) start/stop task nền sạch, không warning
treo; (2) wiring qua `lifespan` thật của `app.main.app` cập nhật
`next_heartbeat_at` bằng scheduler nền, không phải gọi trực tiếp
`run_heartbeat_cycle`.

Không có `pytest-asyncio` trong `requirements-dev.txt` (`pip show
pytest-asyncio` -> not found, xác nhận lúc chạy chunk này) -> kịch bản
(1) dùng `asyncio.run(...)` bọc trong 1 hàm test đồng bộ bình thường
(cùng kỹ thuật script kiểm chứng tạm C3 đã dùng), KHÔNG viết
`async def test_...` (pytest sẽ không tự chạy coroutine đó nếu thiếu
plugin, khiến test "pass" giả mà không thực thi assertion nào).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import warnings

import app.heartbeat.scheduler as scheduler_module

def test_start_and_stop_scheduler_cancels_cleanly_without_warnings(monkeypatch):
    """`start_heartbeat_scheduler`/`stop_heartbeat_scheduler` khởi động và
    huỷ sạch: task chạy được ít nhất 1 vòng, huỷ xong không còn task
    treo/không phát sinh warning nào (bọc `warnings.simplefilter("error")`
    để bắt warning thật - kể cả "Task was destroyed but it is pending" -
    thành lỗi, không chỉ code review bằng mắt).

    Monkeypatch `run_heartbeat_cycle` (tên đã bind vào
    `app.heartbeat.scheduler` qua `from app.heartbeat.service import
    run_heartbeat_cycle`) thành 1 stub đếm số lần gọi - test này chỉ
    quan tâm hành vi start/cancel của scheduler, không phải logic
    heartbeat thật (đã test riêng ở `test_service.py`); dùng stub cũng
    tránh chạm `DEFAULT_DB_PATH` thật (không truyền `db_path` nào ở
    đây)."""
    call_count = 0

    def _stub_run_heartbeat_cycle() -> None:
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(scheduler_module, "run_heartbeat_cycle", _stub_run_heartbeat_cycle)

    async def _scenario() -> "asyncio.Task[None]":
        task = scheduler_module.start_heartbeat_scheduler(poll_interval_seconds=0.05)

        await asyncio.sleep(0.3)
        await scheduler_module.stop_heartbeat_scheduler(task)
        return task

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        task = asyncio.run(_scenario())

    assert task.cancelled()
    assert call_count >= 1

def test_lifespan_wires_real_background_scheduler_updates_next_heartbeat(
    client, simulator_factory, tmp_path
):
    """Wiring qua `lifespan` (`app/main.py`, C3) dùng fixture `client`
    (import lại từ `tests/printers/conftest.py`, đã có monkeypatch trỏ
    CẢ `register_printer` LẪN `run_heartbeat_cycle` về cùng 1 DB tạm -
    xem `conftest.py` của package này): đăng ký 1 máy qua `POST
    /printers`, đợi hơn `SCHEDULER_POLL_INTERVAL_SECONDS` (mặc định
    5.0s - đã dùng đúng giá trị mặc định, đúng cách kịch bản tạm C3 đã
    kiểm chứng thành công, không cần monkeypatch tốc độ), rồi đọc thẳng
    DB tạm -> `next_heartbeat_at` đã được set bởi CHÍNH scheduler nền
    thật chạy qua `lifespan`, KHÔNG phải do gọi trực tiếp
    `run_heartbeat_cycle` như test ở `test_service.py`."""
    handle = simulator_factory(host="127.0.0.1")
    register_response = client.post(
        "/printers",
        json={
            "name": "Scheduler Wiring Printer",
            "ip": handle.host,
            "moonraker_port": handle.port,
        },
    )
    assert register_response.status_code == 201
    printer_id = register_response.json()["id"]

    time.sleep(scheduler_module.SCHEDULER_POLL_INTERVAL_SECONDS + 1.5)

    db_path = tmp_path / "test_printers.db"
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT status, next_heartbeat_at FROM printers WHERE id = ?",
            (printer_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    status, next_heartbeat_at = row
    assert status == "IDLE"
    assert next_heartbeat_at is not None
