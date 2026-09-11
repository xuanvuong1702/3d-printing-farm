"""
Test tích hợp cho route Emergency Stop — `POST
/printers/{printer_id}/emergency_stop` (E3-2/C3) — dùng Moonraker
simulator thật (không mock, cùng pattern `tests/printers/test_control.py`
của E3-1/C3) + DB file tạm qua `run_migrations(tmp_path)` (fixture
`client`, xem `conftest.py`).

Chunk này GỘP luôn Integration & Verification (mục 7
Loop-Controller-Appendix) — không tạo thêm code `app/`, kết luận đối
chiếu AC/D-010/D-013 được ghi vào `docs/Story_E3-2.md` mục
"Integration & Verification".

Đối chiếu AC gốc E3-2 (`docs/Backlog.md`: "Là vận hành viên, tôi muốn
gửi lệnh khẩn cấp (Emergency Stop) cho 1 máy"; AC: "Nút E-Stop riêng
biệt, có xác nhận trước khi gửi"):
- Happy path: `confirm=true` → `200`, `status` trả về là `OFFLINE`
  (đúng D-013 — lệnh đưa Klippy vào trạng thái "shutdown",
  `webhooks.state != 'ready'`) ->
  `test_emergency_stop_happy_path_returns_200_and_offline_status`.
- Thiếu field `confirm` trong body → `422` (fail-safe mặc định
  `confirm: bool = False`, "Quyết định phạm vi" #5, `State_E3-2_v5.md`) ->
  `test_emergency_stop_missing_confirm_returns_422`.
- `confirm=false` tường minh → `422` (cùng nhánh validate ở router,
  KHÔNG phải lỗi validate Pydantic — `EmergencyStopRequest.confirm`
  có default nên body rỗng/`false` đều hợp lệ ở tầng schema, `422` tới
  từ kiểm tra thủ công trong `emergency_stop_printer_endpoint`) ->
  `test_emergency_stop_confirm_false_returns_422`.
- `printer_id` không tồn tại → `404` (cùng tiền lệ PATCH/DELETE E1-3,
  4 route E3-1) -> `test_emergency_stop_not_found_returns_404`.
- Moonraker của máy đích mất kết nối khi gọi lệnh (simulator đã tắt
  sau khi đăng ký) -> `502`, dùng thông báo lỗi từ `PrinterCommandError`
  (`app/printers/service.py`) ->
  `test_emergency_stop_moonraker_error_returns_502`.

Cùng kỹ thuật `_bind_*` đã dùng ở E3-1/C3
(`_bind_control_functions`, `tests/printers/test_control.py`): route
Emergency Stop không đi qua monkeypatch có sẵn của fixture `client`
(chỉ override `register_printer`) — mỗi test tự `monkeypatch.setattr`
thêm `emergency_stop_printer` tại `app.printers.router` để trỏ về
đúng DB tạm của chính test đó, qua hàm `_bind_emergency_stop` bên
dưới (không sửa `conftest.py`/`router.py`/`service.py`).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import emergency_stop_printer as _real_emergency_stop_printer

def _bind_emergency_stop(monkeypatch, tmp_path) -> str:
    """Trỏ `app.printers.router.emergency_stop_printer` (E3-2) về đúng
    DB tạm (`tmp_path / "test_printers.db"`, cùng file mà fixture
    `client` đã dùng cho `register_printer`) — cùng kỹ thuật với
    `_bind_control_functions` (E3-1/C3, `test_control.py`), không sửa
    `conftest.py`/`router.py`/`service.py`."""
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "emergency_stop_printer",
        lambda printer_id: _real_emergency_stop_printer(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-2"
) -> dict:
    """Đăng ký 1 máy qua `POST /printers` (dùng chung cho các test bên
    dưới) - trả về body response đã đăng ký thành công (`id` cần cho
    request Emergency Stop tiếp theo)."""
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def test_emergency_stop_happy_path_returns_200_and_offline_status(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-2: gửi lệnh E-Stop với `confirm=true` tới đúng máy, phản
    hồi trạng thái mới (canonical `OFFLINE`, D-013 — Klippy chuyển
    "shutdown") — xác nhận qua simulator thật, không giả định giá trị
    trả về."""
    _bind_emergency_stop(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    response = client.post(
        f"/printers/{printer_id}/emergency_stop", json={"confirm": True}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "OFFLINE"

def test_emergency_stop_missing_confirm_returns_422(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-2 (fail-safe): thiếu field `confirm` trong body → `422`,
    KHÔNG thực thi lệnh (mặc định `confirm=False` khi field không được
    truyền, "Quyết định phạm vi" #5)."""
    _bind_emergency_stop(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    response = client.post(f"/printers/{printer_id}/emergency_stop", json={})
    assert response.status_code == 422

def test_emergency_stop_confirm_false_returns_422(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-2 (fail-safe): `confirm=false` tường minh → `422`, cùng
    nhánh validate thủ công ở router (không phải lỗi validate
    Pydantic, vì `confirm: bool = False` là giá trị hợp lệ ở tầng
    schema)."""
    _bind_emergency_stop(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    response = client.post(
        f"/printers/{printer_id}/emergency_stop", json={"confirm": False}
    )
    assert response.status_code == 422

def test_emergency_stop_not_found_returns_404(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """AC E3-2 (nhánh lỗi, tiền lệ PATCH/DELETE E1-3, 4 route E3-1):
    `printer_id` không tồn tại → `404`, không phải `500` chung
    chung."""
    _bind_emergency_stop(monkeypatch, tmp_path)
    missing_printer_id = 9999

    response = client.post(
        f"/printers/{missing_printer_id}/emergency_stop", json={"confirm": True}
    )
    assert response.status_code == 404

def test_emergency_stop_moonraker_error_returns_502(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    """AC E3-2 (nhánh lỗi, "Quyết định phạm vi" #7): máy đã đăng ký lúc
    simulator còn sống, nhưng simulator bị tắt trước khi gọi lệnh
    E-Stop -> `PrinterCommandError` (bọc `MoonrakerClientError`) ->
    `502 Bad Gateway`, không phải `500` chung chung."""
    _bind_emergency_stop(monkeypatch, tmp_path)
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(
        client, handle.host, handle.port, name="Printer 502 emergency_stop"
    )
    handle.stop()

    response = client.post(
        f"/printers/{printer['id']}/emergency_stop", json={"confirm": True}
    )
    assert response.status_code == 502
    assert "detail" in response.json()
