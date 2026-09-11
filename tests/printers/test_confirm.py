"""
Test tích hợp cho route "operator confirm" — `POST
/printers/{printer_id}/confirm` (E3-4/C3, D-010 điểm 6) — dùng
Moonraker simulator thật (không mock, cùng pattern
`tests/printers/test_power.py`/`test_emergency_stop.py`) + DB file tạm
qua `run_migrations(tmp_path)` (fixture `client`, xem `conftest.py`).

Chunk này GỘP luôn Integration & Verification (mục 7
Loop-Controller-Appendix) — không tạo thêm code `app/`, kết luận đối
chiếu AC/10 "Quyết định phạm vi" (`docs/State_E3-4_v3.md`) được ghi
vào `docs/Story_E3-4.md` mục "Integration & Verification".

Đối chiếu AC gốc E3-4 (`docs/Backlog.md`: "Là vận hành viên, tôi muốn
máy tự động khoá (`is_held`) chờ tôi xác nhận sau khi 1 job `FINISHED`
hoặc gặp lỗi..."; AC: "job kế tiếp trong queue không dispatch cho tới
khi xác nhận"):
- Happy path: máy đang `is_held=1` -> `POST
  /printers/{id}/confirm` trả `200`, response `is_held: false`,
  `status` KHÔNG đổi so với trước khi gọi (chỉ gỡ hold, không đụng cột
  `status` — khác mọi route điều khiển/power khác) ->
  `test_confirm_happy_path_returns_200_and_clears_is_held`.
- Máy hiện `is_held=0` (không có gì để xác nhận) -> `409 Conflict`
  (raise `PrinterNotHeldError`) ->
  `test_confirm_not_held_returns_409`.
- `printer_id` không tồn tại -> `404`, không phải `500` chung chung
  (cùng tiền lệ PATCH/DELETE E1-3, `emergency_stop`/`power` E3-2/E3-3)
  -> `test_confirm_not_found_returns_404`.
- Idempotency: gọi confirm 2 lần liên tiếp trên cùng 1 máy vừa xác
  nhận xong -> lần 2 phải `409` (KHÔNG tự gỡ lần 2, đúng "route DUY
  NHẤT được phép gỡ `is_held`" chỉ tác động khi máy THẬT SỰ đang bị
  khoá) -> `test_confirm_twice_second_call_returns_409`.

Không có route API nào set `is_held=1` (chỉ `run_heartbeat_cycle`,
E3-4/C1+C2, làm việc đó khi phát hiện transition FINISHED/ERROR có job
active) — helper `_set_is_held` bên dưới ghi thẳng xuống DB tạm để mô
phỏng trạng thái "đang bị khoá" từ trước, cùng kỹ thuật
`_configure_power_capability` đã dùng ở `test_power.py` (E3-3/C4).

Cùng kỹ thuật `_bind_*` đã dùng ở E3-1/C3, E3-2/C4, E3-3/C4: route
confirm không đi qua monkeypatch có sẵn của fixture `client` (chỉ
override `register_printer`) — mỗi test tự `monkeypatch.setattr` thêm
`confirm_printer` tại `app.printers.router` để trỏ về đúng DB tạm của
chính test đó, qua hàm `_bind_confirm` bên dưới (không sửa
`conftest.py`/`router.py`/`service.py`).
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import confirm_printer as _real_confirm_printer

_STATUS_BEFORE_CONFIRM = "FINISHED"

def _bind_confirm(monkeypatch, tmp_path) -> str:
    """Trỏ `app.printers.router.confirm_printer` (E3-4/C3) về đúng DB
    tạm (`tmp_path / "test_printers.db"`, cùng file mà fixture `client`
    đã dùng cho `register_printer`) — cùng kỹ thuật với
    `_bind_power`/`_bind_emergency_stop` (E3-3/C4, E3-2/C4), không sửa
    `conftest.py`/`router.py`/`service.py`."""
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "confirm_printer",
        lambda printer_id: _real_confirm_printer(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-4"
) -> dict:
    """Đăng ký 1 máy qua `POST /printers` (dùng chung cho các test bên
    dưới) - trả về body response đã đăng ký thành công (`id` cần cho
    request confirm tiếp theo)."""
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _set_is_held(db_path: str, printer_id: int, *, is_held: bool, status: str) -> None:
    """Ghi thẳng xuống DB tạm để mô phỏng máy đã bị khoá (`is_held=1`)
    từ trước, kèm `status` tương ứng — xem docstring module này cho lý
    do không dùng được route API nào (chỉ `run_heartbeat_cycle` set
    được `is_held=1`, ngoài phạm vi test tích hợp route này)."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET is_held = ?, status = ? WHERE id = ?",
            (1 if is_held else 0, status, printer_id),
        )
        connection.commit()
    finally:
        connection.close()

def test_confirm_happy_path_returns_200_and_clears_is_held(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-4 (D-010 điểm 6): máy đang `is_held=1` -> `POST
    /printers/{id}/confirm` trả `200`, `is_held` gỡ về `false`, `status`
    KHÔNG đổi so với trước khi gọi (chỉ gỡ hold, không đụng cột
    `status`)."""
    db_path = _bind_confirm(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _set_is_held(db_path, printer_id, is_held=True, status=_STATUS_BEFORE_CONFIRM)

    response = client.post(f"/printers/{printer_id}/confirm")
    assert response.status_code == 200
    body = response.json()
    assert body["is_held"] is False
    assert body["status"] == _STATUS_BEFORE_CONFIRM

def test_confirm_not_held_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-4 (nhánh lỗi, D-010 điểm 6): máy hiện `is_held=0` (không có
    gì để xác nhận) -> `409 Conflict`."""
    db_path = _bind_confirm(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _set_is_held(db_path, printer_id, is_held=False, status="IDLE")

    response = client.post(f"/printers/{printer_id}/confirm")
    assert response.status_code == 409
    assert "detail" in response.json()

def test_confirm_not_found_returns_404(client: TestClient, tmp_path, monkeypatch) -> None:
    """AC E3-4 (nhánh lỗi, tiền lệ PATCH/DELETE E1-3, `emergency_stop`/
    `power` E3-2/E3-3): `printer_id` không tồn tại -> `404`, không phải
    `500` chung chung."""
    _bind_confirm(monkeypatch, tmp_path)
    missing_printer_id = 9999

    response = client.post(f"/printers/{missing_printer_id}/confirm")
    assert response.status_code == 404

def test_confirm_twice_second_call_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-4 (idempotency, D-010 điểm 6): gọi confirm 2 lần liên tiếp
    trên cùng 1 máy vừa xác nhận xong -> lần 2 phải `409` (KHÔNG tự gỡ
    lần 2 — route chỉ được phép gỡ `is_held` khi máy THẬT SỰ đang bị
    khoá)."""
    db_path = _bind_confirm(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _set_is_held(db_path, printer_id, is_held=True, status=_STATUS_BEFORE_CONFIRM)

    first_response = client.post(f"/printers/{printer_id}/confirm")
    assert first_response.status_code == 200

    second_response = client.post(f"/printers/{printer_id}/confirm")
    assert second_response.status_code == 409
    assert "detail" in second_response.json()
