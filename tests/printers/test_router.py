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

AC gốc E1-3 ("CRUD đầy đủ, không xoá được máy đang có job active",
`docs/Backlog.md`) — xem `docs/State_E1-3_v2.md`/`docs/Story_E1-3.md`
cho rationale phạm vi đầy đủ, không lặp lại ở đây:
- `PATCH /printers/{printer_id}` đổi `name`/`model`/`api_key` (kể cả
  `null` tường minh) -> `test_patch_printer_updates_name`,
  `test_patch_printer_updates_model_and_api_key_including_explicit_null`.
- Body rỗng là no-op hợp lệ, `updated_at` không đổi ->
  `test_patch_printer_empty_body_is_noop_and_keeps_updated_at`.
- `printer_id` không tồn tại -> `404` ->
  `test_patch_printer_not_found_returns_404`.
- `ip`/`moonraker_port` không sửa được qua PATCH (field lạ bị Pydantic
  bỏ qua) -> `test_patch_printer_ignores_ip_field`.
- `DELETE /printers/{printer_id}` thành công (hard delete, `204`) ->
  `test_delete_printer_success_returns_204_and_removes_row`.
- `printer_id` không tồn tại -> `404` ->
  `test_delete_printer_not_found_returns_404`.
- Chặn xoá khi `status` đang `PRINTING`/`PAUSED` ("có job active",
  D-013/D-010, `409`, không xoá) ->
  `test_delete_printer_blocked_when_status_is_printing`,
  `test_delete_printer_blocked_when_status_is_paused`.
- Chặn xoá khi còn dòng `jobs` tham chiếu `printer_id` (lớp bảo vệ bổ
  sung từ `PRAGMA foreign_keys = ON`, `409`, khác thông báo với case
  "có job active") -> `test_delete_printer_blocked_by_related_job_row`.

`PATCH`/`DELETE /printers/{printer_id}` cũng không đi qua monkeypatch
có sẵn của fixture `client` (chỉ override `register_printer`) — mỗi
test PATCH/DELETE tự `monkeypatch.setattr` thêm
`printers_router_module.update_printer`/`delete_printer` để trỏ về
đúng DB tạm của chính test đó, qua hàm `_bind_update_and_delete_printer`
bên dưới (cùng kỹ thuật với `_bind_list_printers` của E1-2/C2 — không
sửa fixture `client` trong `conftest.py`). Case "có job active"/"còn
dòng `jobs` liên quan" UPDATE/INSERT trực tiếp qua
`sqlite3.connect(db_path)` ngay trong thân test (chưa có cách set qua
API công khai, Epic 4 Job Queue chưa triển khai).
"""

from __future__ import annotations

import sqlite3
import time

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import delete_printer as _real_delete_printer
from app.printers.service import list_printers as _real_list_printers
from app.printers.service import update_printer as _real_update_printer

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

def _bind_update_and_delete_printer(monkeypatch, tmp_path) -> str:
    """Trỏ `app.printers.router.update_printer`/`delete_printer`
    (E1-3) về đúng DB tạm (`tmp_path / "test_printers.db"`) - cùng kỹ
    thuật với `_bind_list_printers` (E1-2/C2), không sửa
    `conftest.py`/`router.py`/`service.py`."""
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "update_printer",
        lambda printer_id, request: _real_update_printer(
            printer_id, request, db_path=db_path
        ),
    )
    monkeypatch.setattr(
        printers_router_module,
        "delete_printer",
        lambda printer_id: _real_delete_printer(printer_id, db_path=db_path),
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

def _register_one_printer(client: TestClient, simulator: int, name: str = "Printer E1-3") -> dict:
    """Đăng ký 1 máy qua `POST /printers` (dùng chung cho các test
    PATCH/DELETE bên dưới) - trả về body response đã đăng ký thành
    công (`id` cần cho các request PATCH/DELETE tiếp theo)."""
    response = client.post(
        "/printers",
        json={"name": name, "ip": "127.0.0.1", "moonraker_port": simulator},
    )
    assert response.status_code == 201
    return response.json()

def test_patch_printer_updates_name(client: TestClient, simulator: int, tmp_path, monkeypatch) -> None:
    """AC E1-3: PATCH sửa `name` -> 200, đúng dữ liệu mới, `updated_at`
    thay đổi so với lúc đăng ký."""
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    time.sleep(1.1)

    response = client.patch(f"/printers/{registered['id']}", json={"name": "Printer Renamed"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Printer Renamed"
    assert body["updated_at"] != registered["updated_at"]

    assert body["ip"] == registered["ip"]

def test_patch_printer_updates_model_and_api_key_including_explicit_null(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E1-3: PATCH sửa `model`/`api_key`, kể cả truyền `null` tường
    minh để xoá giá trị hiện có về `NULL` (cả 2 cột đều nullable ở
    DDL) - phân biệt được với "không truyền" nhờ `exclude_unset`."""
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    response = client.post(
        "/printers",
        json={
            "name": "Printer With Model",
            "ip": "127.0.0.1",
            "moonraker_port": simulator,
            "model": "QIDI Plus4",
            "api_key": "secret-key",
        },
    )
    assert response.status_code == 201
    printer_id = response.json()["id"]

    patch_response = client.patch(
        f"/printers/{printer_id}",
        json={"model": "QIDI X-Max3", "api_key": None},
    )

    assert patch_response.status_code == 200
    body = patch_response.json()
    assert body["model"] == "QIDI X-Max3"
    assert body["api_key"] is None

def test_patch_printer_empty_body_is_noop_and_keeps_updated_at(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E1-3 (quyết định phạm vi C0): PATCH body rỗng là no-op hợp lệ
    -> 200, dữ liệu giữ nguyên, `updated_at` KHÔNG đổi (khác PATCH có
    field thật sự thay đổi)."""
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    first_patch = client.patch(f"/printers/{registered['id']}", json={"name": "Printer First Patch"})
    assert first_patch.status_code == 200
    updated_at_after_real_patch = first_patch.json()["updated_at"]

    empty_patch = client.patch(f"/printers/{registered['id']}", json={})

    assert empty_patch.status_code == 200
    body = empty_patch.json()
    assert body["name"] == "Printer First Patch"
    assert body["updated_at"] == updated_at_after_real_patch

def test_patch_printer_not_found_returns_404(client: TestClient, tmp_path, monkeypatch) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)

    response = client.patch("/printers/999999", json={"name": "Ghost Printer"})

    assert response.status_code == 404
    assert "detail" in response.json()

def test_patch_printer_ignores_ip_field(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E1-3 (quyết định phạm vi C0): `ip`/`moonraker_port` KHÔNG sửa
    được qua PATCH - `PrinterUpdateRequest` không khai báo field này
    nên FastAPI/Pydantic bỏ qua field lạ trong body, không raise lỗi."""
    _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    response = client.patch(
        f"/printers/{registered['id']}",
        json={"name": "Printer IP Unchanged", "ip": "10.0.0.99"},
    )

    assert response.status_code == 200
    assert response.json()["ip"] == registered["ip"]

def test_delete_printer_success_returns_204_and_removes_row(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E1-3: DELETE 1 máy không có job active (status mặc định
    `UNKNOWN`) -> 204 (body rỗng), hard delete - dòng bị xoá khỏi DB."""
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)
    assert _count_printers(db_path) == 1

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert _count_printers(db_path) == 0

def test_delete_printer_not_found_returns_404(client: TestClient, tmp_path, monkeypatch) -> None:
    _bind_update_and_delete_printer(monkeypatch, tmp_path)

    response = client.delete("/printers/999999")

    assert response.status_code == 404
    assert "detail" in response.json()

def _set_printer_status(db_path: str, printer_id: int, status_value: str) -> None:
    """UPDATE trực tiếp cột `status` của 1 máy qua kết nối SQLite riêng
    - chưa có cách set `status` PRINTING/PAUSED qua API công khai (Epic
    4 Job Queue chưa triển khai), dùng trực tiếp DB tạm của test."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE printers SET status = ? WHERE id = ?", (status_value, printer_id)
        )
        connection.commit()
    finally:
        connection.close()

def test_delete_printer_blocked_when_status_is_printing(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E1-3: "không xoá được máy đang có job active" - status
    PRINTING (D-013/D-010) -> 409, KHÔNG xoá."""
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)
    _set_printer_status(db_path, registered["id"], "PRINTING")

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 409
    assert "detail" in response.json()
    assert _count_printers(db_path) == 1

def test_delete_printer_blocked_when_status_is_paused(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """Giống case PRINTING - PAUSED cũng thuộc định nghĩa "có job
    active" đã chốt ở D-013/D-010 -> 409, KHÔNG xoá."""
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)
    _set_printer_status(db_path, registered["id"], "PAUSED")

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 409
    assert _count_printers(db_path) == 1

def test_delete_printer_blocked_by_related_job_row(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """`PRAGMA foreign_keys = ON` (dùng nhất quán từ E1-1) khiến DELETE
    raise `IntegrityError` khi còn dòng `jobs` tham chiếu `printer_id`
    - map sang 409 như lớp bảo vệ bổ sung (khác thông báo với case "có
    job active"), dù bảng `jobs` luôn rỗng ở giai đoạn thực tế hiện tại
    (Epic 4 chưa triển khai) - case này insert thủ công để mô phỏng.
    `status` của máy vẫn giữ mặc định (không phải PRINTING/PAUSED) để
    chắc chắn đây là nhánh IntegrityError, không phải has_active_job."""
    db_path = _bind_update_and_delete_printer(monkeypatch, tmp_path)
    registered = _register_one_printer(client, simulator)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO jobs (printer_id, filename) VALUES (?, ?)",
            (registered["id"], "test-model.gcode"),
        )
        connection.commit()
    finally:
        connection.close()

    response = client.delete(f"/printers/{registered['id']}")

    assert response.status_code == 409
    assert "detail" in response.json()
    assert _count_printers(db_path) == 1
