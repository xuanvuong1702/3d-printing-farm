"""
Test tích hợp cho `POST /printers` (E1-1/C2) và `GET /printers`
(E1-2/C2) — dùng Moonraker simulator thật (không mock, giống pattern
`tests/drivers/test_base_driver.py` của E0-6) + DB file tạm qua
`run_migrations(tmp_path)` (fixture `client`, xem `conftest.py`).

Đối chiếu đúng 3 gạch đầu dòng AC gốc E1-1 (`docs/Backlog.md`):
- API lưu IP/moonraker_port (mặc định 7125)/tên/model/api_key (tuỳ
  chọn) -> `test_register_printer_success`,
  `test_register_printer_optional_fields_omitted`,
  `test_moonraker_port_defaults_to_7125_when_omitted`.
- Validate kết nối được tới Moonraker trước khi lưu -> AC này bao gồm
  cả 2 vế: "kết nối được" (case thành công, có kiểm tra count trong DB)
  và "trả lỗi rõ ràng nếu không kết nối được, không insert" ->
  `test_register_printer_connection_failure_returns_422_and_does_not_insert`.
- Tự động dò + lưu phiên bản Moonraker/Klipper + capabilities ->
  `test_register_printer_success` (assert đầy đủ 3 field).

Thêm case IP trùng (409, không insert thêm) tuy không phải 1 trong 3
gạch đầu dòng AC nhưng đã được chốt cụ thể trong chunk plan C1/C2 (xem
`State_E1-1_v2.md`/`v3.md`) vì dựa trên `UNIQUE` constraint có sẵn của
cột `ip` (E0-4) -> `test_duplicate_ip_returns_409_and_does_not_insert_again`.

AC gốc E1-2 (`GET /printers` trả list + trạng thái) — poll-on-request
qua HTTP (quyết định phạm vi C0, xem `docs/State_E1-2_v2.md`):
- List rỗng khi chưa đăng ký máy nào ->
  `test_list_printers_empty_when_none_registered`.
- 1 máy đang chạy simulator -> status không phải OFFLINE, DB cũng được
  UPDATE -> `test_list_printers_returns_online_status_and_updates_db`.
- 1 máy hết simulator (tắt giữa chừng) -> status trả OFFLINE, DB cũng
  OFFLINE -> `test_list_printers_returns_offline_when_simulator_stopped`
  (dùng fixture mới `simulator_factory`, xem `conftest.py`, vì fixture
  `simulator` cũ không cho tắt giữa chừng trong thân test).
- Trộn 1 máy online + 1 máy offline trong cùng 1 response, không máy
  nào làm hỏng response của máy khác ->
  `test_list_printers_mixed_online_and_offline_printers` (2 simulator
  trên 2 địa chỉ loopback khác nhau, `127.0.0.1`/`127.0.0.2` - bắt buộc
  vì cột `ip` có `UNIQUE` constraint, không phải `(ip, port)`).

`GET /printers` không đi qua monkeypatch có sẵn của fixture `client`
(fixture đó chỉ override `register_printer`, không override
`list_printers` — xem `conftest.py`) — mỗi test `GET /printers` tự
`monkeypatch.setattr` thêm `printers_router_module.list_printers` để
trỏ về đúng DB tạm của chính test đó, qua hàm `_bind_list_printers`
bên dưới (không sửa fixture `client` trong `conftest.py`, chỉ dùng
`monkeypatch`/`tmp_path` - 2 fixture built-in của pytest - ngay trong
thân test).
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import list_printers as _real_list_printers

def _count_printers(db_path: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM printers").fetchone()[0]
    finally:
        connection.close()

def _printer_status_by_ip(db_path: str) -> dict:
    """`{ip: status}` cho toàn bộ bảng `printers` - dùng để đối chiếu
    write-through của `list_printers` (E1-2) mà không phụ thuộc thứ tự
    `id` trả về."""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT ip, status FROM printers").fetchall()
        return dict(rows)
    finally:
        connection.close()

def _bind_list_printers(monkeypatch, tmp_path) -> str:
    """Trỏ `app.printers.router.list_printers` (E1-2) về đúng DB tạm
    (`tmp_path / "test_printers.db"`, cùng file mà fixture `client` đã
    dùng cho `register_printer`) - không sửa `conftest.py`/`router.py`/
    `service.py`, chỉ monkeypatch tại tầng test giống hệt cách fixture
    `client` đã làm với `register_printer`."""
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "list_printers",
        lambda: _real_list_printers(db_path=db_path),
    )
    return db_path

def test_register_printer_success(client: TestClient, simulator: int) -> None:
    response = client.post(
        "/printers",
        json={
            "name": "Printer A",
            "ip": "127.0.0.1",
            "moonraker_port": simulator,
            "model": "QIDI Plus4",
            "api_key": "secret-key",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["name"] == "Printer A"
    assert body["ip"] == "127.0.0.1"
    assert body["moonraker_port"] == simulator
    assert body["model"] == "QIDI Plus4"
    assert body["api_key"] == "secret-key"

    assert body["moonraker_version"] == "v0.9.3-simulated"

    assert body["klipper_version"] == "v0.12.0-simulated"
    assert body["capabilities"] == [
        "klippy_connection",
        "file_manager",
        "job_queue",
    ]

    assert body["status"] == "UNKNOWN"
    assert body["is_held"] is False
    assert body["created_at"]
    assert body["updated_at"]

def test_register_printer_optional_fields_omitted(
    client: TestClient, simulator: int
) -> None:
    """`model`/`api_key` là tuỳ chọn (D-003) - không truyền vẫn đăng ký được."""
    response = client.post(
        "/printers",
        json={"name": "Printer B", "ip": "127.0.0.1", "moonraker_port": simulator},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["model"] is None
    assert body["api_key"] is None

def test_moonraker_port_defaults_to_7125_when_omitted(
    client: TestClient, simulator_on_default_port: int
) -> None:
    """AC: `moonraker_port` mặc định 7125 (D-001) khi không truyền trong request."""
    response = client.post(
        "/printers", json={"name": "Printer Default Port", "ip": "127.0.0.1"}
    )

    assert response.status_code == 201
    assert response.json()["moonraker_port"] == 7125 == simulator_on_default_port

def test_register_printer_connection_failure_returns_422_and_does_not_insert(
    client: TestClient, tmp_path
) -> None:
    """Không có gì lắng nghe ở port này -> lỗi kết nối rõ ràng (422), không phải 500."""
    unused_port = 65000

    response = client.post(
        "/printers",
        json={
            "name": "Printer Unreachable",
            "ip": "127.0.0.1",
            "moonraker_port": unused_port,
        },
    )

    assert response.status_code == 422
    assert "detail" in response.json()

    db_path = str(tmp_path / "test_printers.db")
    assert _count_printers(db_path) == 0

def test_duplicate_ip_returns_409_and_does_not_insert_again(
    client: TestClient, simulator: int, tmp_path
) -> None:
    ip = "127.0.0.1"
    first = client.post(
        "/printers",
        json={"name": "Printer C", "ip": ip, "moonraker_port": simulator},
    )
    assert first.status_code == 201

    second = client.post(
        "/printers",
        json={"name": "Printer C duplicate", "ip": ip, "moonraker_port": simulator},
    )

    assert second.status_code == 409
    assert "detail" in second.json()

    db_path = str(tmp_path / "test_printers.db")
    assert _count_printers(db_path) == 1

def test_root_route_still_works_after_including_printers_router(
    client: TestClient,
) -> None:
    """Sửa `app/main.py` (include router mới) không đổi endpoint `/` (E0-1)."""
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_list_printers_empty_when_none_registered(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """AC E1-2: chưa đăng ký máy nào -> `GET /printers` trả 200 + []."""
    _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    assert response.json() == []

def test_list_printers_returns_online_status_and_updates_db(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E1-2: máy đang chạy simulator -> status không phải OFFLINE, cột
    `status` trong DB cũng được UPDATE (write-through, D-013)."""
    ip = "127.0.0.1"
    register_response = client.post(
        "/printers",
        json={"name": "Printer Online", "ip": ip, "moonraker_port": simulator},
    )
    assert register_response.status_code == 201

    assert register_response.json()["status"] == "UNKNOWN"

    db_path = _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1

    assert body[0]["status"] == "IDLE"
    assert body[0]["ip"] == ip

    assert _printer_status_by_ip(db_path)[ip] == "IDLE"

def test_list_printers_returns_offline_when_simulator_stopped(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    """AC E1-2: máy đã đăng ký lúc simulator còn sống, nhưng simulator bị
    tắt trước khi gọi `GET /printers` -> status trả OFFLINE (lỗi kết nối
    hẳn, map tường minh ở `list_printers`, KHÔNG phải nhánh OFFLINE có
    sẵn của `get_status()` khi Klippy chưa sẵn sàng), DB cũng OFFLINE."""
    handle = simulator_factory(host="127.0.0.1")
    ip = handle.host

    register_response = client.post(
        "/printers",
        json={"name": "Printer Offline", "ip": ip, "moonraker_port": handle.port},
    )
    assert register_response.status_code == 201

    handle.stop()

    db_path = _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "OFFLINE"
    assert body[0]["ip"] == ip

    assert _printer_status_by_ip(db_path)[ip] == "OFFLINE"

def test_list_printers_mixed_online_and_offline_printers(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    """AC E1-2: trộn 1 máy online + 1 máy offline trong cùng 1 response -
    không máy nào làm hỏng response của máy khác (không phải lỗi 500,
    không thiếu phần tử nào). 2 địa chỉ loopback khác nhau vì cột `ip`
    có `UNIQUE` constraint (không phải `(ip, port)`)."""
    online_handle = simulator_factory(host="127.0.0.1")
    offline_handle = simulator_factory(host="127.0.0.2")

    online_ip = online_handle.host
    offline_ip = offline_handle.host

    online_register = client.post(
        "/printers",
        json={
            "name": "Printer Mixed Online",
            "ip": online_ip,
            "moonraker_port": online_handle.port,
        },
    )
    assert online_register.status_code == 201

    offline_register = client.post(
        "/printers",
        json={
            "name": "Printer Mixed Offline",
            "ip": offline_ip,
            "moonraker_port": offline_handle.port,
        },
    )
    assert offline_register.status_code == 201

    offline_handle.stop()

    db_path = _bind_list_printers(monkeypatch, tmp_path)

    response = client.get("/printers")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2

    status_by_ip = {item["ip"]: item["status"] for item in body}
    assert status_by_ip[online_ip] == "IDLE"
    assert status_by_ip[offline_ip] == "OFFLINE"

    db_status_by_ip = _printer_status_by_ip(db_path)
    assert db_status_by_ip[online_ip] == "IDLE"
    assert db_status_by_ip[offline_ip] == "OFFLINE"
