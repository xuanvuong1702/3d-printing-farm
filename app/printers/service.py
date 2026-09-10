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
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from app.db.migrate import DEFAULT_DB_PATH
from app.drivers import resolve_driver
from app.moonraker.http_client import MoonrakerClientError
from app.printers.schemas import PrinterCreateRequest, PrinterResponse

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
