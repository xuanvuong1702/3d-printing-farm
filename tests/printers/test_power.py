"""
Test tích hợp cho 2 route Power API — `POST
/printers/{printer_id}/power/on` và `POST
/printers/{printer_id}/power/off` (E3-3/C3) — dùng Moonraker simulator
thật (không mock, cùng pattern `tests/printers/test_emergency_stop.py`
của E3-2/C4) + DB file tạm qua `run_migrations(tmp_path)` (fixture
`client`, xem `conftest.py`).

Chunk này GỘP luôn Integration & Verification (mục 7
Loop-Controller-Appendix) — không tạo thêm code `app/`, kết luận đối
chiếu AC/12 "Quyết định phạm vi" (`docs/State_E3-3_v2.md`) được ghi
vào `docs/Story_E3-3.md` mục "Integration & Verification".

Đối chiếu AC gốc E3-3 (`docs/Backlog.md`: "Là vận hành viên, tôi muốn
bật/tắt nguồn máy in từ xa (nếu có smart plug)"; AC: "Tích hợp
Machine/Power API, cấu hình theo từng máy; chức năng chỉ hiển thị cho
máy có component `power` trong `capabilities` đã ghi nhận (E1-1), ẩn
với máy không hỗ trợ"):
- Happy path `power_on`: máy có capability `power` + đã cấu hình
  `power_device_name` -> `200` ->
  `test_power_on_happy_path_returns_200`.
- Happy path `power_off`: cùng máy, simulator vẫn phản hồi được sau
  lệnh -> `200`, `status` phản ánh đúng `get_status()` sau lệnh ->
  `test_power_off_happy_path_returns_200`.
- Máy KHÔNG có capability `power` -> `409` ->
  `test_power_on_not_supported_returns_409`.
- Máy CÓ capability `power` nhưng CHƯA cấu hình `power_device_name`
  (`NULL`) -> `409` (thông điệp khác case trên) ->
  `test_power_on_not_configured_returns_409`.
- `printer_id` không tồn tại -> `404` ->
  `test_power_on_not_found_returns_404`.
- Moonraker mất kết nối lúc gọi `set_device_power` (simulator đã tắt
  trước khi gọi route, cùng kỹ thuật
  `test_emergency_stop_moonraker_error_returns_502`) -> `502` ->
  `test_power_on_moonraker_error_returns_502`.
- Riêng nhánh `power_off`: `get_status()` lỗi SAU KHI `set_power` đã
  thành công -> response vẫn `200` + `status="OFFLINE"` (KHÔNG `502`)
  -> `test_power_off_get_status_error_after_success_maps_offline`.

Simulator KHÔNG bao giờ trả component `"power"` trong `GET
/server/info` (`tools/moonraker_simulator/app.py::server_info`, danh
sách cố định `["klippy_connection", "file_manager", "job_queue"]`) —
nên KHÔNG thể tạo máy có capability `power` chỉ qua `POST /printers`
thông thường. Helper `_configure_power_capability` bên dưới ghi thẳng
xuống DB tạm (cùng `db_path` mà `client`/`_bind_power` đã dùng) để mô
phỏng máy "đã ghi nhận" capability `power` từ trước (E1-1) — không có
route API nào sửa được cột `capabilities` (chỉ `power_device_name` sửa
được qua `PATCH /printers/{printer_id}`, nhưng dùng thẳng SQL cho gọn,
nhất quán 1 chỗ ghi cấu hình test).

Cùng kỹ thuật `_bind_*` đã dùng ở E3-1/C3 và E3-2/C4: route Power API
không đi qua monkeypatch có sẵn của fixture `client` (chỉ override
`register_printer`) — mỗi test tự `monkeypatch.setattr` thêm
`power_on_printer`/`power_off_printer` tại `app.printers.router` để
trỏ về đúng DB tạm của chính test đó, qua hàm `_bind_power` bên dưới
(không sửa `conftest.py`/`router.py`/`service.py`).

Case `power_off` sau khi `set_power` thành công (case cuối) dùng
monkeypatch riêng trên `app.drivers.base.BaseKlipperDriver.get_status`
(class-level, vì `_resolve_driver_for_row` tự dựng driver mới mỗi lần
gọi — không có instance cố định để patch) để raise
`MoonrakerClientError` CHỈ ở bước đọc lại status, không ảnh hưởng lệnh
`set_power` chính (đã gọi thật qua simulator, thành công trước đó) —
KHÔNG dùng được kỹ thuật "tắt simulator trước khi gọi route" (case
502) vì lệnh `set_power` cũng sẽ lỗi theo, sai mục đích test.
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.drivers.base import BaseKlipperDriver
from app.moonraker.http_client import MoonrakerClientError
from app.printers.service import power_off_printer as _real_power_off_printer
from app.printers.service import power_on_printer as _real_power_on_printer

_POWER_DEVICE_NAME = "printer"

def _bind_power(monkeypatch, tmp_path) -> str:
    """Trỏ `app.printers.router.power_on_printer`/`power_off_printer`
    (E3-3) về đúng DB tạm (`tmp_path / "test_printers.db"`, cùng file
    mà fixture `client` đã dùng cho `register_printer`) — cùng kỹ
    thuật với `_bind_emergency_stop` (E3-2/C4, `test_emergency_stop.py`),
    không sửa `conftest.py`/`router.py`/`service.py`."""
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "power_on_printer",
        lambda printer_id: _real_power_on_printer(printer_id, db_path=db_path),
    )
    monkeypatch.setattr(
        printers_router_module,
        "power_off_printer",
        lambda printer_id: _real_power_off_printer(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-3"
) -> dict:
    """Đăng ký 1 máy qua `POST /printers` (dùng chung cho các test bên
    dưới) - trả về body response đã đăng ký thành công (`id` cần cho
    request Power API tiếp theo)."""
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def _configure_power_capability(
    db_path: str,
    printer_id: int,
    *,
    supported: bool,
    device_name: Optional[str],
) -> None:
    """Ghi thẳng xuống DB tạm để mô phỏng máy đã ghi nhận capability
    `power` (E1-1) từ trước — xem docstring module này cho lý do không
    dùng được `POST /printers` thông thường (simulator không bao giờ
    trả component `power`)."""
    capabilities: List[str] = ["power"] if supported else []
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET capabilities = ?, power_device_name = ? "
            "WHERE id = ?",
            (json.dumps(capabilities), device_name, printer_id),
        )
        connection.commit()
    finally:
        connection.close()

def test_power_on_happy_path_returns_200(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-3: máy có capability `power` + đã cấu hình
    `power_device_name` -> `POST /printers/{id}/power/on` trả `200`."""
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 200
    body = response.json()
    assert body["power_device_name"] == _POWER_DEVICE_NAME

def test_power_off_happy_path_returns_200(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-3: cùng máy, `POST /printers/{id}/power/off` trả `200`,
    `status` phản ánh đúng kết quả `get_status()` sau lệnh (simulator
    vẫn phản hồi được - nhánh happy path bình thường, khác case
    `get_status()` lỗi sau đó bên dưới)."""
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )

    response = client.post(f"/printers/{printer_id}/power/off")
    assert response.status_code == 200
    body = response.json()
    assert body["power_device_name"] == _POWER_DEVICE_NAME
    assert body["status"]

def test_power_on_not_supported_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-3 (nhánh lỗi, "Quyết định phạm vi" #8): máy KHÔNG có
    capability `power` -> `409 Conflict`."""
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(db_path, printer_id, supported=False, device_name=None)

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 409
    assert "detail" in response.json()

def test_power_on_not_configured_returns_409(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-3 (nhánh lỗi, "Quyết định phạm vi" #8): máy CÓ capability
    `power` nhưng CHƯA cấu hình `power_device_name` (`NULL`) -> `409
    Conflict` (thông điệp khác case không hỗ trợ)."""
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(db_path, printer_id, supported=True, device_name=None)

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 409
    assert "detail" in response.json()

def test_power_on_not_found_returns_404(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """AC E3-3 (nhánh lỗi, tiền lệ PATCH/DELETE E1-3, `emergency_stop`
    E3-2): `printer_id` không tồn tại -> `404`, không phải `500` chung
    chung."""
    _bind_power(monkeypatch, tmp_path)
    missing_printer_id = 9999

    response = client.post(f"/printers/{missing_printer_id}/power/on")
    assert response.status_code == 404

def test_power_on_moonraker_error_returns_502(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    """AC E3-3 (nhánh lỗi, "Quyết định phạm vi" #8): máy đã đăng ký lúc
    simulator còn sống, nhưng simulator bị tắt trước khi gọi lệnh
    Power API -> `PrinterCommandError` (bọc `MoonrakerClientError`) ->
    `502 Bad Gateway`, không phải `500` chung chung."""
    db_path = _bind_power(monkeypatch, tmp_path)
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(
        client, handle.host, handle.port, name="Printer 502 power_on"
    )
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )
    handle.stop()

    response = client.post(f"/printers/{printer_id}/power/on")
    assert response.status_code == 502
    assert "detail" in response.json()

def test_power_off_get_status_error_after_success_maps_offline(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-3 (nhánh lỗi, "Quyết định phạm vi" #9): `get_status()` lỗi
    SAU KHI `set_power("off")` đã gọi thật thành công qua simulator ->
    response vẫn `200` + `status="OFFLINE"` (KHÔNG `502`) - dự kiến mô
    phỏng mất kết nối do cắt điện board, KHÔNG phải lỗi của bản thân
    lệnh Power API.

    Monkeypatch `BaseKlipperDriver.get_status` (class-level, vì
    `_resolve_driver_for_row` tự dựng driver mới mỗi lần gọi trong
    `app/printers/service.py::_run_power_command` - không có instance
    cố định để patch riêng) để raise `MoonrakerClientError` - `set_power`
    KHÔNG bị ảnh hưởng (method khác, vẫn gọi thật qua simulator)."""
    db_path = _bind_power(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]
    _configure_power_capability(
        db_path, printer_id, supported=True, device_name=_POWER_DEVICE_NAME
    )

    def _get_status_raises(self) -> None:
        raise MoonrakerClientError("simulated: mất kết nối sau khi cắt điện board")

    monkeypatch.setattr(BaseKlipperDriver, "get_status", _get_status_raises)

    response = client.post(f"/printers/{printer_id}/power/off")
    assert response.status_code == 200
    assert response.json()["status"] == "OFFLINE"
