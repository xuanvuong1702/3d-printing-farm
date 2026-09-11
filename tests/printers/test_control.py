"""
Test tích hợp cho 4 route điều khiển job — `POST
/printers/{printer_id}/print/{start,pause,resume,cancel}` (E3-1/C3) —
dùng Moonraker simulator thật (không mock, cùng pattern
`tests/printers/test_router.py`) + DB file tạm qua
`run_migrations(tmp_path)` (fixture `client`, xem `conftest.py`).

Chunk này GỘP luôn Integration & Verification (mục 7
Loop-Controller-Appendix) — không tạo thêm code `app/`, kết luận đối
chiếu AC/D-010/D-013 được ghi vào `docs/Story_E3-1.md` mục
"Integration & Verification".

Đối chiếu AC gốc E3-1 (`docs/Backlog.md`: "Thao tác qua UI → gọi đúng
máy → phản hồi trạng thái mới"):
- Happy path cả 4 thao tác trên 1 máy thật (simulator) trong đúng thứ
  tự nghiệp vụ hợp lý (start → pause → resume → cancel), mỗi bước xác
  nhận `status` trả về đúng giá trị canonical tương ứng (D-013) ->
  `test_start_pause_resume_cancel_happy_path_updates_status_each_step`.
- `printer_id` không tồn tại -> `404` cho cả 4 route ->
  `test_start_pause_resume_cancel_not_found_returns_404`.
- Moonraker của máy đích mất kết nối khi gọi lệnh (simulator đã tắt
  sau khi đăng ký) -> `502` cho cả 4 route, dùng thông báo lỗi từ
  `PrinterCommandError` (`app/printers/service.py`) ->
  `test_start_pause_resume_cancel_moonraker_error_returns_502`.

Cùng kỹ thuật `_bind_*` đã dùng ở E1-2/C2 (`_bind_list_printers`) và
E1-3/C2 (`_bind_update_and_delete_printer`) trong
`tests/printers/test_router.py`: 4 route điều khiển job không đi qua
monkeypatch có sẵn của fixture `client` (chỉ override
`register_printer`) — mỗi test tự `monkeypatch.setattr` thêm
`printers_router_module.start_print`/`pause_print`/`resume_print`/
`cancel_print` để trỏ về đúng DB tạm của chính test đó, qua hàm
`_bind_control_functions` bên dưới (không sửa
`conftest.py`/`router.py`/`service.py`).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.printers.router as printers_router_module
from app.printers.service import cancel_print as _real_cancel_print
from app.printers.service import pause_print as _real_pause_print
from app.printers.service import resume_print as _real_resume_print
from app.printers.service import start_print as _real_start_print

def _bind_control_functions(monkeypatch, tmp_path) -> str:
    """Trỏ `app.printers.router.start_print`/`pause_print`/
    `resume_print`/`cancel_print` (E3-1) về đúng DB tạm
    (`tmp_path / "test_printers.db"`, cùng file mà fixture `client` đã
    dùng cho `register_printer`) - cùng kỹ thuật với
    `_bind_update_and_delete_printer` (E1-3/C2, `test_router.py`),
    không sửa `conftest.py`/`router.py`/`service.py`."""
    db_path = str(tmp_path / "test_printers.db")
    monkeypatch.setattr(
        printers_router_module,
        "start_print",
        lambda printer_id, filename, file_content: _real_start_print(
            printer_id, filename, file_content, db_path=db_path
        ),
    )
    monkeypatch.setattr(
        printers_router_module,
        "pause_print",
        lambda printer_id: _real_pause_print(printer_id, db_path=db_path),
    )
    monkeypatch.setattr(
        printers_router_module,
        "resume_print",
        lambda printer_id: _real_resume_print(printer_id, db_path=db_path),
    )
    monkeypatch.setattr(
        printers_router_module,
        "cancel_print",
        lambda printer_id: _real_cancel_print(printer_id, db_path=db_path),
    )
    return db_path

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E3-1"
) -> dict:
    """Đăng ký 1 máy qua `POST /printers` (dùng chung cho các test bên
    dưới) - trả về body response đã đăng ký thành công (`id` cần cho
    các request điều khiển job tiếp theo)."""
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def test_start_pause_resume_cancel_happy_path_updates_status_each_step(
    client: TestClient, simulator: int, tmp_path, monkeypatch
) -> None:
    """AC E3-1: mỗi thao tác gọi đúng máy, phản hồi đúng trạng thái mới
    (canonical, D-013) — xác nhận qua chuỗi thao tác thật trên 1 máy
    (simulator), không giả định giá trị trả về."""
    _bind_control_functions(monkeypatch, tmp_path)
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    start_response = client.post(
        f"/printers/{printer_id}/print/start",
        files={"file": ("test.gcode", b"G28\nG1 X10 Y10\n")},
    )
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "PRINTING"

    pause_response = client.post(f"/printers/{printer_id}/print/pause")
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "PAUSED"

    resume_response = client.post(f"/printers/{printer_id}/print/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "PRINTING"

    cancel_response = client.post(f"/printers/{printer_id}/print/cancel")
    assert cancel_response.status_code == 200

    assert cancel_response.json()["status"] == "STOPPED"

def test_start_pause_resume_cancel_not_found_returns_404(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """AC E3-1 (nhánh lỗi, tiền lệ PATCH/DELETE E1-3): `printer_id`
    không tồn tại -> `404` cho cả 4 route, không phải `500` chung
    chung."""
    _bind_control_functions(monkeypatch, tmp_path)
    missing_printer_id = 9999

    start_response = client.post(
        f"/printers/{missing_printer_id}/print/start",
        files={"file": ("test.gcode", b"G28\n")},
    )
    assert start_response.status_code == 404

    for action in ("pause", "resume", "cancel"):
        response = client.post(f"/printers/{missing_printer_id}/print/{action}")
        assert response.status_code == 404

def test_start_pause_resume_cancel_moonraker_error_returns_502(
    client: TestClient, simulator_factory, tmp_path, monkeypatch
) -> None:
    """AC E3-1 (nhánh lỗi, "Quyết định phạm vi" #5): máy đã đăng ký lúc
    simulator còn sống, nhưng simulator bị tắt trước khi gọi lệnh điều
    khiển -> `PrinterCommandError` (bọc `MoonrakerClientError`) ->
    `502 Bad Gateway`, không phải `500` chung chung — cho cả 4 route,
    mỗi route dùng 1 máy đăng ký riêng (tránh phụ thuộc trạng thái máy
    còn lại sau lỗi ở route trước)."""
    _bind_control_functions(monkeypatch, tmp_path)

    for index, action in enumerate(("start", "pause", "resume", "cancel")):
        handle = simulator_factory(host=f"127.0.0.{index + 1}")
        printer = _register_one_printer(
            client, handle.host, handle.port, name=f"Printer 502 {action}"
        )
        handle.stop()

        if action == "start":
            response = client.post(
                f"/printers/{printer['id']}/print/start",
                files={"file": ("test.gcode", b"G28\n")},
            )
        else:
            response = client.post(f"/printers/{printer['id']}/print/{action}")

        assert response.status_code == 502
        assert "detail" in response.json()
