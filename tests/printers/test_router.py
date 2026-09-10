"""
Test tích hợp cho `POST /printers` (E1-1/C2) — dùng Moonraker simulator
thật (không mock, giống pattern `tests/drivers/test_base_driver.py` của
E0-6) + DB file tạm qua `run_migrations(tmp_path)` (fixture `client`,
xem `conftest.py`).

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
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

def _count_printers(db_path: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM printers").fetchone()[0]
    finally:
        connection.close()

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
