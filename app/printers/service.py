"""
Logic nghiệp vụ cho `POST /printers` (E1-1/C1).

Luồng `register_printer`:
1. `resolve_driver(model=..., firmware_version=None, host=ip, ...)` — chưa
   biết firmware lúc đăng ký nên luôn rơi vào tier (c) `BaseKlipperDriver`
   mặc định (nhất quán D-012 mục 3; "Ma trận tương thích" hiện còn
   trống, xem `Decisions.md`).
2. `driver.get_server_info()` — validate kết nối tới Moonraker + lấy
   `moonraker_version`/`components` (capabilities, D-007).
3. `driver.get_printer_info()` — lấy `software_version` (klipper_version,
   D-007).
4. Nếu (2)/(3) raise `MoonrakerClientError` → raise `PrinterConnectionError`
   ở đây (tầng router map sang HTTP 422) — KHÔNG insert vào DB.
5. `INSERT` vào bảng `printers`. IP trùng (`UNIQUE` constraint có sẵn ở
   schema, E0-4) → bắt `sqlite3.IntegrityError`, raise
   `PrinterAlreadyExistsError` (tầng router map sang HTTP 409).

Quyết định cục bộ chốt tại chunk này (không phải `D-00X`, xem
`docs/Story_E1-1.md` mục "Quyết định kỹ thuật áp dụng..." cho rationale
đầy đủ): KHÔNG gọi `resolve_driver` lần 2 với `firmware_version` thật
vừa dò được ở bước (3). AC gốc chỉ yêu cầu lưu version/capabilities
"dùng để chọn driver phù hợp" — không yêu cầu resolve driver ngay lúc
đăng ký; tầng service gọi lệnh thật (story sau) tự
`resolve_driver(model, klipper_version, ...)` khi cần dùng driver.
Hệ quả: `klipper_version` được lưu NGUYÊN BẢN gốc (có tiền tố `v` nếu
Moonraker/Klipper trả vậy, ví dụ simulator trả `"v0.12.0-simulated"`) —
không `lstrip`/chuẩn hoá ở đây; việc xử lý tiền tố `v` cho
`version_in_range` (nếu cần) là trách nhiệm của bất kỳ call site nào
sau này thật sự gọi lại `resolve_driver` với `firmware_version` đã dò
được.

Driver không bao giờ chạm DB (CLAUDE.md nguyên tắc #2, D-006) — mọi
INSERT/SELECT ở đây, KHÔNG nằm trong `app/drivers/`.

Luồng `update_printer`/`delete_printer` (E1-3/C1) — xem
`docs/State_E1-3_v2.md` mục "Quyết định phạm vi chốt tại chunk C0" cho
rationale đầy đủ (không lặp lại ở đây): KHÔNG gọi `resolve_driver`/
Moonraker nào cả (sửa/xoá thông tin mô tả thuần tuý trong DB, khác
`register_printer`/`list_printers`). `delete_printer` KHÔNG dùng
exception cho nhánh nghiệp vụ "có job active" (dự kiến trước, không
phải lỗi validate input như `PrinterConnectionError`/
`PrinterAlreadyExistsError`) — trả về 1 trong 4 giá trị dạng chuỗi rõ
nghĩa (`"deleted"`/`"not_found"`/`"has_active_job"`/
`"has_related_records"`) để router tự map status code.

Luồng `list_printers` (E1-2/C1) — poll-on-request qua HTTP, xem
`docs/State_E1-2_v2.md` mục "Quyết định phạm vi chốt tại chunk C0" cho
rationale đầy đủ (kênh WS bền/push thật để dành E2-1, KHÔNG lặp lại ở
đây):
1. SELECT toàn bộ bảng `printers`.
2. Với mỗi máy: `resolve_driver(model, firmware_version=klipper_version
   đã lưu, host=ip, port=moonraker_port, api_key)` (dùng nguyên cơ chế
   resolution đã khoá từ E0-6, D-006/D-012 — KHÔNG viết lại), rồi gọi
   `driver.get_status()` — một hình thức "ping" HTTP thuần (D-002 phần
   1) phản ánh trạng thái tại đúng thời điểm request, không phải push
   liên tục.
3. Nếu bản thân lệnh gọi thất bại hẳn (`MoonrakerClientError` — không
   kết nối được, khác với "kết nối được nhưng Klippy chưa sẵn sàng" mà
   `get_status()` đã tự map `OFFLINE` sẵn, D-013) → tầng này bắt
   exception và map tường minh sang `OFFLINE`. 1 máy lỗi không được làm
   hỏng response chứa các máy khác.
4. Ghi đè (UPDATE) cột `status` (+ `updated_at`, cột này KHÔNG có
   trigger tự động ở DDL, `app/db/schema.py`) trước khi trả response —
   write-through, D-013 "canonical status" là nguồn sự thật chung.
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from app.db.migrate import DEFAULT_DB_PATH
from app.drivers import resolve_driver
from app.moonraker.http_client import MoonrakerClientError
from app.printers.schemas import (
    PrinterCreateRequest,
    PrinterResponse,
    PrinterUpdateRequest,
)

_INSERT_PRINTER_SQL = """
INSERT INTO printers (
    name, ip, moonraker_port, model, api_key,
    moonraker_version, klipper_version, capabilities
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_PRINTER_BY_ID_SQL = """
SELECT id, name, ip, moonraker_port, model, api_key,
       moonraker_version, klipper_version, capabilities,
       status, is_held, created_at, updated_at
FROM printers WHERE id = ?
"""

_SELECT_ALL_PRINTER_CONN_INFO_SQL = """
SELECT id, ip, moonraker_port, model, api_key, klipper_version
FROM printers
"""

_UPDATE_PRINTER_STATUS_SQL = """
UPDATE printers
SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

class PrinterConnectionError(Exception):
    """Không kết nối/validate được Moonraker của máy vừa khai báo đăng ký."""

class PrinterAlreadyExistsError(Exception):
    """IP đã được đăng ký trước đó (UNIQUE constraint của bảng `printers`)."""

def register_printer(
    request: PrinterCreateRequest, db_path: str = DEFAULT_DB_PATH
) -> PrinterResponse:
    """Đăng ký 1 máy in mới — validate kết nối + capability detection + INSERT."""
    driver = resolve_driver(
        model=request.model,
        firmware_version=None,
        host=request.ip,
        port=request.moonraker_port,
        api_key=request.api_key,
    )

    try:
        server_info = driver.get_server_info()
        printer_info = driver.get_printer_info()
    except MoonrakerClientError as exc:
        raise PrinterConnectionError(
            f"Không kết nối/xác nhận được Moonraker tại "
            f"{request.ip}:{request.moonraker_port}: {exc}"
        ) from exc

    moonraker_version: Optional[str] = server_info.get("moonraker_version")
    capabilities: List[str] = server_info.get("components", [])
    klipper_version: Optional[str] = printer_info.get("software_version")
    capabilities_json = json.dumps(capabilities)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            cursor = connection.execute(
                _INSERT_PRINTER_SQL,
                (
                    request.name,
                    request.ip,
                    request.moonraker_port,
                    request.model,
                    request.api_key,
                    moonraker_version,
                    klipper_version,
                    capabilities_json,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PrinterAlreadyExistsError(
                f"IP {request.ip!r} đã được đăng ký trước đó."
            ) from exc

        connection.commit()
        row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (cursor.lastrowid,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(row)

def list_printers(db_path: str = DEFAULT_DB_PATH) -> List[PrinterResponse]:
    """Danh sách toàn bộ máy đã đăng ký kèm trạng thái poll-on-request
    (E1-2/C1) — xem docstring module này và
    `docs/State_E1-2_v2.md` để biết rationale đầy đủ. Không raise cho lỗi
    kết nối của từng máy riêng lẻ (map sang `OFFLINE`, ghi log qua giá
    trị trả về, không phải exception)."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        conn_rows = connection.execute(_SELECT_ALL_PRINTER_CONN_INFO_SQL).fetchall()

        responses: List[PrinterResponse] = []
        for printer_id, ip, moonraker_port, model, api_key, klipper_version in conn_rows:
            driver = resolve_driver(
                model=model,
                firmware_version=klipper_version,
                host=ip,
                port=moonraker_port,
                api_key=api_key,
            )
            try:
                canonical_status = driver.get_status().canonical_status
            except MoonrakerClientError:

                canonical_status = "OFFLINE"

            connection.execute(
                _UPDATE_PRINTER_STATUS_SQL, (canonical_status, printer_id)
            )
            connection.commit()

            updated_row = connection.execute(
                _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
            ).fetchone()
            responses.append(_row_to_response(updated_row))
    finally:
        connection.close()

    return responses

def update_printer(
    printer_id: int,
    request: PrinterUpdateRequest,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PrinterResponse]:
    """Sửa `name`/`model`/`api_key` của 1 máy đã đăng ký (E1-3/C1). Trả
    `None` nếu không tìm thấy `printer_id` (tầng router map sang HTTP
    404). Body rỗng (`exclude_unset=True` trả dict rỗng) là no-op hợp
    lệ — trả `200` + dữ liệu hiện tại, KHÔNG đụng `updated_at`.

    Dùng `request.model_dump(exclude_unset=True)` để build câu `UPDATE`
    chỉ với field thật sự được truyền (kể cả truyền `null` tường
    minh) — tên field của `PrinterUpdateRequest` khớp trực tiếp tên
    cột `printers` nên dùng thẳng làm tên cột trong câu `UPDATE` (an
    toàn: keys bị giới hạn cứng bởi chính schema Pydantic, không phải
    input tự do từ người dùng).
    """
    fields = request.model_dump(exclude_unset=True)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        existing = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
        if existing is None:
            return None

        if fields:
            set_clauses = ", ".join(f"{column} = ?" for column in fields)
            update_sql = (
                f"UPDATE printers SET {set_clauses}, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                "WHERE id = ?"
            )
            connection.execute(update_sql, (*fields.values(), printer_id))
            connection.commit()
            row = connection.execute(
                _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
            ).fetchone()
        else:
            row = existing
    finally:
        connection.close()

    return _row_to_response(row)

def delete_printer(printer_id: int, db_path: str = DEFAULT_DB_PATH) -> str:
    """Xoá cứng (hard delete) 1 máy in (E1-3/C1). Trả về 1 trong 4 giá
    trị (xem docstring module này):
    - `"deleted"`: xoá thành công.
    - `"not_found"`: không có `printer_id` này (router map 404).
    - `"has_active_job"`: `status` đang `PRINTING`/`PAUSED` (định nghĩa
      "có job active" đã chốt ở D-013/D-010) — chặn xoá (router map
      409), không xoá.
    - `"has_related_records"`: `PRAGMA foreign_keys = ON` khiến
      `DELETE` raise `sqlite3.IntegrityError` vì còn dòng `jobs`/
      `job_history` tham chiếu `printer_id` này — lớp bảo vệ bổ sung
      (bảng `jobs` luôn rỗng ở giai đoạn hiện tại, Epic 4 chưa triển
      khai) (router map 409).
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(
            "SELECT status FROM printers WHERE id = ?", (printer_id,)
        ).fetchone()
        if row is None:
            return "not_found"

        (current_status,) = row
        if current_status in ("PRINTING", "PAUSED"):
            return "has_active_job"

        try:
            connection.execute("DELETE FROM printers WHERE id = ?", (printer_id,))
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            return "has_related_records"
    finally:
        connection.close()

    return "deleted"

def _row_to_response(row: tuple) -> PrinterResponse:
    """Chuyển 1 dòng SQL thô (thứ tự cột theo `_SELECT_PRINTER_BY_ID_SQL`)
    sang `PrinterResponse` — giải mã `capabilities` (JSON TEXT) thành
    `list`, `is_held` (0/1) thành `bool`."""
    (
        id_,
        name,
        ip,
        moonraker_port,
        model,
        api_key,
        moonraker_version,
        klipper_version,
        capabilities_json,
        status,
        is_held,
        created_at,
        updated_at,
    ) = row
    return PrinterResponse(
        id=id_,
        name=name,
        ip=ip,
        moonraker_port=moonraker_port,
        model=model,
        api_key=api_key,
        moonraker_version=moonraker_version,
        klipper_version=klipper_version,
        capabilities=json.loads(capabilities_json),
        status=status,
        is_held=bool(is_held),
        created_at=created_at,
        updated_at=updated_at,
    )
