"""
Test unit cho hàm nghiệp vụ `upload_file_to_printer`
(`app/printers/service.py`, E4-1/C2) — CHƯA có route HTTP ở chunk này
(thuộc C3), nên test gọi thẳng hàm service, KHÔNG qua `TestClient`/
router (khác `tests/printers/test_control.py` và các file
`test_*.py` khác trong package này, vốn đều test qua route đã có).

Dùng `client` fixture (`conftest.py`) CHỈ để đăng ký máy qua `POST
/printers` (route đã có từ E1-1, không liên quan gì tới upload) — lấy
`printer_id` hợp lệ gắn với simulator thật, cùng DB tạm
(`tmp_path / "test_printers.db"`) mà fixture đó đã tạo qua
`run_migrations(tmp_path)`. Từ đó gọi `upload_file_to_printer(...,
db_path=db_path)` trực tiếp — không monkeypatch router (chưa có route
nào cho C2 mà cần override).

Đối chiếu AC gốc E4-1 + "Quyết định phạm vi" (`docs/State_E4-1_v3.md`):
- Từ chối file không phải `.gcode` TRƯỚC khi chạm DB/Moonraker (điểm 3)
  -> `test_upload_rejects_non_gcode_extension_before_touching_db`.
- `printer_id` không tồn tại -> trả `None`, không tạo `jobs` nào (cùng
  pattern các hàm service khác) ->
  `test_upload_printer_not_found_returns_none`.
- Happy path: upload thành công -> job `status='queued'` + đúng
  `file_size_bytes`/`estimated_print_seconds` lấy từ
  `get_file_metadata` (điểm 4+5) ->
  `test_upload_happy_path_creates_queued_job_with_metadata`.
- Lỗi Moonraker (simulator đã tắt sau khi đăng ký) -> raise
  `PrinterCommandError` (không nuốt lỗi gốc), job vừa tạo được UPDATE
  sang `status='failed'` (không xoá, giữ log lỗi) (điểm 4+6) ->
  `test_upload_moonraker_error_raises_and_marks_job_failed`.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db.migrate import run_migrations
from app.printers.service import (
    PrinterCommandError,
    UnsupportedFileTypeError,
    upload_file_to_printer,
)

_GCODE_CONTENT = b"G28\nG1 X10 Y10\n"

def _register_one_printer(
    client: TestClient, ip: str, port: int, name: str = "Printer E4-1"
) -> dict:
    """Đăng ký 1 máy qua `POST /printers` (route đã có từ E1-1, không
    liên quan tới upload) — trả về body response, `id` dùng làm
    `printer_id` cho các lệnh gọi `upload_file_to_printer` tiếp theo."""
    response = client.post(
        "/printers", json={"name": name, "ip": ip, "moonraker_port": port}
    )
    assert response.status_code == 201
    return response.json()

def test_upload_rejects_non_gcode_extension_before_touching_db(tmp_path) -> None:
    """Điểm 3 "Quyết định phạm vi": sai định dạng (không phải `.gcode`,
    case-insensitive) -> raise `UnsupportedFileTypeError` NGAY, không
    tốn 1 request upload thật/không chạm DB — `db_path` trỏ tới 1 file
    chưa từng chạy migration, xác nhận hàm không hề mở kết nối DB nào
    trước khi validate xong."""
    never_migrated_db_path = str(tmp_path / "never_migrated.db")

    with pytest.raises(UnsupportedFileTypeError):
        upload_file_to_printer(
            printer_id=1,
            filename="model.stl",
            file_content=b"not gcode",
            db_path=never_migrated_db_path,
        )

    with pytest.raises(UnsupportedFileTypeError):
        upload_file_to_printer(
            printer_id=1,
            filename="MODEL.GCODE.STL",
            file_content=b"not gcode",
            db_path=never_migrated_db_path,
        )

def test_upload_accepts_gcode_extension_case_insensitively(tmp_path) -> None:
    """Phần mở rộng `.gcode` chấp nhận không phân biệt hoa/thường (điểm
    3) — chỉ xác nhận không raise `UnsupportedFileTypeError` ngay bước
    validate; `printer_id` không tồn tại nên dừng lại ở `None` ngay sau
    đó (không cần simulator cho test này)."""
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    result = upload_file_to_printer(
        printer_id=9999,
        filename="MODEL.GCODE",
        file_content=_GCODE_CONTENT,
        db_path=db_path,
    )

    assert result is None

def test_upload_printer_not_found_returns_none(tmp_path) -> None:
    """Điểm phổ biến ở mọi hàm service: `printer_id` không tồn tại ->
    trả `None` (router C3 sẽ map 404), KHÔNG tạo bản ghi `jobs` nào."""
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    result = upload_file_to_printer(
        printer_id=9999,
        filename="test.gcode",
        file_content=_GCODE_CONTENT,
        db_path=db_path,
    )

    assert result is None

    connection = sqlite3.connect(db_path)
    try:
        (job_count,) = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
    finally:
        connection.close()
    assert job_count == 0

def test_upload_happy_path_creates_queued_job_with_metadata(
    client: TestClient, simulator: int, tmp_path
) -> None:
    """Happy path (điểm 4+5 "Quyết định phạm vi"): upload thành công ->
    job `status='queued'`, `file_size_bytes` khớp đúng kích thước file
    đã upload, `estimated_print_seconds` là số nguyên suy ra từ
    `get_file_metadata` (simulator, `_SIMULATED_SECONDS_PER_BYTE`)."""
    db_path = str(tmp_path / "test_printers.db")
    printer = _register_one_printer(client, "127.0.0.1", simulator)
    printer_id = printer["id"]

    result = upload_file_to_printer(
        printer_id=printer_id,
        filename="test.gcode",
        file_content=_GCODE_CONTENT,
        db_path=db_path,
    )

    assert result is not None
    assert result["printer_id"] == printer_id
    assert result["filename"] == "test.gcode"
    assert result["status"] == "queued"
    assert result["file_size_bytes"] == len(_GCODE_CONTENT)
    assert isinstance(result["estimated_print_seconds"], int)
    assert result["estimated_print_seconds"] >= 0

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT status, file_size_bytes, estimated_print_seconds "
            "FROM jobs WHERE id = ?",
            (result["id"],),
        ).fetchone()
    finally:
        connection.close()
    assert row == (
        "queued",
        len(_GCODE_CONTENT),
        result["estimated_print_seconds"],
    )

def test_upload_moonraker_error_raises_and_marks_job_failed(
    client: TestClient, simulator_factory, tmp_path
) -> None:
    """Nhánh lỗi (điểm 4+6): máy đã đăng ký lúc simulator còn sống,
    nhưng simulator bị tắt (`handle.stop()`) trước khi gọi upload ->
    `MoonrakerClientError` -> raise `PrinterCommandError` (KHÔNG nuốt
    lỗi gốc), job vừa tạo được UPDATE sang `status='failed'` (giữ lại,
    không xoá — audit trail tối thiểu, cùng tinh thần "Quyết định phạm
    vi" điểm 4)."""
    db_path = str(tmp_path / "test_printers.db")
    handle = simulator_factory(host="127.0.0.1")
    printer = _register_one_printer(client, handle.host, handle.port)
    printer_id = printer["id"]
    handle.stop()

    with pytest.raises(PrinterCommandError):
        upload_file_to_printer(
            printer_id=printer_id,
            filename="test.gcode",
            file_content=_GCODE_CONTENT,
            db_path=db_path,
        )

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT status FROM jobs WHERE printer_id = ?", (printer_id,)
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("failed",)]
