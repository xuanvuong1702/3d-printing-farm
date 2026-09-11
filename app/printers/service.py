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

Luồng `start_print`/`pause_print`/`resume_print`/`cancel_print`
(E3-1/C1) — xem `docs/State_E3-1_v2.md` mục "Quyết định phạm vi" cho
rationale đầy đủ (không lặp lại ở đây): mỗi hàm SELECT thông tin kết
nối máy theo `printer_id` (tái dùng `_SELECT_PRINTER_BY_ID_SQL`), trả
`None` nếu không tìm thấy (cùng pattern `update_printer`);
`resolve_driver(model, firmware_version=klipper_version đã lưu, host,
port, api_key)` (D-012, tái dùng nguyên cơ chế đã khoá từ E0-6); gọi
đúng method driver tương ứng. `MoonrakerClientError` từ lệnh gọi đó →
raise `PrinterCommandError` (tầng router map sang HTTP 502). Thành
công → đọc lại `driver.get_status().canonical_status`, UPDATE
`printers.status`, SELECT lại, trả `_row_to_response(row)` — tái dùng
đúng pattern write-through đã có ở `list_printers`.

Luồng `emergency_stop_printer` (E3-2/C3) — xem `docs/State_E3-2_v4.md`
mục "Quyết định phạm vi" cho rationale đầy đủ (không lặp lại ở đây):
tái dùng nguyên `_run_print_command`/`_resolve_driver_for_row` đã có
từ E3-1/C1, gọi `driver.emergency_stop()` thay vì
`upload_and_print`/`pause_job`/`resume_job`/`cancel_job`. `is_held`
(D-010) KHÔNG liên quan (E-Stop là hành động operator chủ động xác
nhận, không phải máy tự chuyển trạng thái ngoài ý muốn).

Luồng `power_on_printer`/`power_off_printer` (E3-3/C3) — xem
`docs/State_E3-3_v2.md` mục "Quyết định phạm vi" điểm 8/9 cho
rationale đầy đủ (không lặp lại ở đây): KHÔNG tái dùng thẳng
`_run_print_command` (khác `emergency_stop_printer`) vì luồng lỗi
khác hẳn — viết riêng `_run_power_command` dùng chung cho 2 hàm này.
Thứ tự kiểm tra TRƯỚC khi chạm Moonraker: `printer_id` không tồn tại
→ trả `None` (router map 404); `capabilities` không chứa `"power"` →
raise `PrinterPowerNotSupportedError` (router map 409); có capability
nhưng `power_device_name` là `NULL` → raise
`PrinterPowerNotConfiguredError` (router map 409, thông điệp khác
trường hợp trên). Lệnh `driver.set_power(...)` lỗi
(`MoonrakerClientError`) → raise `PrinterCommandError` (router map
502, cùng tiền lệ mọi lệnh điều khiển khác). SAU KHI lệnh chính đã
thành công, đọc lại status khác nhau theo `action`: `"on"` gọi
`driver.get_status()` bình thường (không bắt riêng exception — chấp
nhận rủi ro Klippy chưa kịp khởi động, ghi ở "Giới hạn/known issue"
`Story_E3-3.md`); `"off"` bắt riêng `MoonrakerClientError` từ
`get_status()` (dự kiến mất kết nối vì đã cắt điện board) và map
thẳng `canonical_status = "OFFLINE"` — KHÔNG raise lỗi vì bản thân
lệnh Power API đã thành công.

Luồng `confirm_printer` (E3-4/C3) — xem `docs/State_E3-4_v2.md` mục
"Quyết định phạm vi" #6 cho rationale đầy đủ (không lặp lại ở đây):
SELECT máy theo `printer_id` (trả `None` nếu không có, router map
404); `is_held = 0` → raise `PrinterNotHeldError` (router map 409,
không có gì để xác nhận); `is_held = 1` → UPDATE `is_held = 0`
(KHÔNG đụng cột `status`, khác mọi hàm điều khiển/power ở trên vốn
luôn ghi đè `status`), trả `PrinterResponse` mới nhất. Đây là route
DUY NHẤT (ngoài ngoại lệ tự gỡ hold ở `run_heartbeat_cycle` khi hồi
phục `PRINTING` có job thật, `app/heartbeat/service.py`) được phép
gỡ `is_held` (nguyên tắc #1 `CLAUDE.md`, D-010).
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
       power_device_name, status, is_held, created_at, updated_at
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

class PrinterCommandError(Exception):
    """Lỗi khi gọi lệnh điều khiển job (start/pause/resume/cancel) qua
    driver xuống Moonraker của máy đích — bọc lại `MoonrakerClientError`
    (E3-1/C1, tầng router map sang HTTP 502 Bad Gateway)."""

class PrinterPowerNotSupportedError(Exception):
    """Máy không có component `power` trong `capabilities` (D-007) —
    Power API không áp dụng cho máy này (E3-3/C3, tầng router map sang
    HTTP 409 Conflict)."""

class PrinterPowerNotConfiguredError(Exception):
    """Máy CÓ capability `power` nhưng `power_device_name` chưa được cấu
    hình qua `PATCH /printers/{printer_id}` (E3-3/C3, tầng router map
    sang HTTP 409 Conflict — thông điệp khác
    `PrinterPowerNotSupportedError`)."""

class PrinterNotHeldError(Exception):
    """Máy hiện KHÔNG đang ở trạng thái `is_held = 1` — không có gì để
    vận hành viên xác nhận (E3-4/C3, D-010 điểm 6, tầng router map sang
    HTTP 409 Conflict)."""

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

def _resolve_driver_for_row(row: tuple):
    """Dựng driver cho 1 máy từ dòng `_SELECT_PRINTER_BY_ID_SQL` (E3-1/C1)
    — tái dùng đúng cơ chế `resolve_driver` (D-012) đã dùng ở
    `list_printers`, truyền `firmware_version=klipper_version` đã lưu."""
    (
        _id,
        _name,
        ip,
        moonraker_port,
        model,
        api_key,
        _moonraker_version,
        klipper_version,
        _capabilities_json,
        _power_device_name,
        _status,
        _is_held,
        _created_at,
        _updated_at,
    ) = row
    return resolve_driver(
        model=model,
        firmware_version=klipper_version,
        host=ip,
        port=moonraker_port,
        api_key=api_key,
    )

def _run_print_command(
    printer_id: int, db_path: str, command
) -> Optional[PrinterResponse]:
    """Khung dùng chung cho 4 hàm điều khiển job (E3-1/C1): SELECT máy
    theo `printer_id` (trả `None` nếu không có, router map 404), dựng
    driver, gọi `command(driver)` — bắt `MoonrakerClientError` raise
    `PrinterCommandError` (router map 502) — rồi đọc lại status, UPDATE
    DB, trả `PrinterResponse` (write-through, cùng pattern
    `list_printers`)."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        driver = _resolve_driver_for_row(row)
        try:
            command(driver)
            canonical_status = driver.get_status().canonical_status
        except MoonrakerClientError as exc:
            raise PrinterCommandError(
                f"Lỗi khi gọi lệnh điều khiển job trên máy id={printer_id}: {exc}"
            ) from exc

        connection.execute(_UPDATE_PRINTER_STATUS_SQL, (canonical_status, printer_id))
        connection.commit()
        updated_row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(updated_row)

def start_print(
    printer_id: int,
    filename: str,
    file_content: bytes,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PrinterResponse]:
    """Upload G-code + in ngay trên máy `printer_id` (E3-1/C1, AC "start").
    Trả `None` nếu không tìm thấy `printer_id`. Raise `PrinterCommandError`
    nếu Moonraker của máy đích lỗi/mất kết nối khi gọi lệnh."""
    return _run_print_command(
        printer_id,
        db_path,
        lambda driver: driver.upload_and_print(filename, file_content),
    )

def pause_print(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """Tạm dừng job đang in trên máy `printer_id` (E3-1/C1, AC "pause").
    Trả `None` nếu không tìm thấy `printer_id`. Raise `PrinterCommandError`
    nếu Moonraker của máy đích lỗi/mất kết nối khi gọi lệnh."""
    return _run_print_command(printer_id, db_path, lambda driver: driver.pause_job())

def resume_print(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """Tiếp tục job đang tạm dừng trên máy `printer_id` (E3-1/C1, AC
    "resume"). Trả `None` nếu không tìm thấy `printer_id`. Raise
    `PrinterCommandError` nếu Moonraker của máy đích lỗi/mất kết nối khi
    gọi lệnh."""
    return _run_print_command(printer_id, db_path, lambda driver: driver.resume_job())

def cancel_print(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """Huỷ job đang in trên máy `printer_id` (E3-1/C1, AC "cancel"). Trả
    `None` nếu không tìm thấy `printer_id`. Raise `PrinterCommandError`
    nếu Moonraker của máy đích lỗi/mất kết nối khi gọi lệnh."""
    return _run_print_command(printer_id, db_path, lambda driver: driver.cancel_job())

def emergency_stop_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """Gửi lệnh Emergency Stop (E-Stop) tới máy `printer_id` (E3-2/C3,
    AC gốc E3-2). Tái dùng đúng khung `_run_print_command` đã có từ
    E3-1/C1 (không viết lại) — SELECT máy theo `printer_id` (trả `None`
    nếu không tìm thấy, router map 404), dựng driver, gọi
    `driver.emergency_stop()` — bắt `MoonrakerClientError` raise
    `PrinterCommandError` (router map 502) — rồi đọc lại status (dự
    kiến `OFFLINE`, D-013, vì lệnh đưa Klippy vào trạng thái
    "shutdown"), UPDATE DB, trả `PrinterResponse`.

    `is_held` (D-010) KHÔNG liên quan tới hàm này — xem
    `docs/State_E3-2_v4.md` mục "Quyết định phạm vi" #8: E-Stop là
    hành động operator chủ động xác nhận, không cần cơ chế `is_held`
    để "nhắc xác nhận lại". Hàm này không đọc/ghi cột `is_held` ngoài
    việc SELECT chung (`_SELECT_PRINTER_BY_ID_SQL`) và map response
    (`_row_to_response`), giống 4 hàm điều khiển job ở trên."""
    return _run_print_command(
        printer_id, db_path, lambda driver: driver.emergency_stop()
    )

def _run_power_command(
    printer_id: int, action: str, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """Khung dùng chung cho `power_on_printer`/`power_off_printer`
    (E3-3/C3) — KHÔNG tái dùng `_run_print_command` vì luồng lỗi khác
    hẳn (xem docstring module, "Quyết định phạm vi" #9 ở
    `docs/State_E3-3_v2.md`).

    Thứ tự kiểm tra TRƯỚC khi chạm Moonraker: `printer_id` không tồn
    tại → trả `None` (router map 404); `capabilities` không chứa
    `"power"` → raise `PrinterPowerNotSupportedError` (409);
    `power_device_name` là `NULL` → raise
    `PrinterPowerNotConfiguredError` (409, thông điệp khác trên).
    `driver.set_power(...)` lỗi → raise `PrinterCommandError` (502).

    SAU KHI lệnh chính đã thành công, đọc lại status khác nhau theo
    `action`: `"on"` gọi `driver.get_status()` bình thường (không bắt
    riêng exception); `"off"` bắt riêng `MoonrakerClientError` từ
    `get_status()` và map thẳng `canonical_status = "OFFLINE"` — KHÔNG
    raise lỗi vì bản thân lệnh Power API đã thành công.
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        capabilities_json = row[8]
        power_device_name = row[9]
        capabilities: List[str] = json.loads(capabilities_json)
        if "power" not in capabilities:
            raise PrinterPowerNotSupportedError(
                f"Máy id={printer_id} không có capability 'power', không hỗ trợ "
                "bật/tắt nguồn từ xa."
            )
        if power_device_name is None:
            raise PrinterPowerNotConfiguredError(
                f"Máy id={printer_id} có capability 'power' nhưng chưa cấu hình "
                "power_device_name (PATCH /printers/{printer_id})."
            )

        driver = _resolve_driver_for_row(row)
        try:
            driver.set_power(power_device_name, action)
        except MoonrakerClientError as exc:
            raise PrinterCommandError(
                f"Lỗi khi gọi lệnh Power API trên máy id={printer_id}: {exc}"
            ) from exc

        if action == "off":
            try:
                canonical_status = driver.get_status().canonical_status
            except MoonrakerClientError:

                canonical_status = "OFFLINE"
        else:
            canonical_status = driver.get_status().canonical_status

        connection.execute(_UPDATE_PRINTER_STATUS_SQL, (canonical_status, printer_id))
        connection.commit()
        updated_row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(updated_row)

def power_on_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """Bật nguồn máy in `printer_id` qua Machine/Power API (AC gốc E3-3).
    Trả `None` nếu không tìm thấy `printer_id`. Raise
    `PrinterPowerNotSupportedError`/`PrinterPowerNotConfiguredError` nếu
    máy không hỗ trợ/chưa cấu hình. Raise `PrinterCommandError` nếu
    Moonraker của máy đích lỗi khi gọi lệnh bật nguồn."""
    return _run_power_command(printer_id, "on", db_path)

def power_off_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """Tắt nguồn máy in `printer_id` qua Machine/Power API (AC gốc E3-3).
    Trả `None` nếu không tìm thấy `printer_id`. Raise
    `PrinterPowerNotSupportedError`/`PrinterPowerNotConfiguredError` nếu
    máy không hỗ trợ/chưa cấu hình. Raise `PrinterCommandError` nếu
    Moonraker của máy đích lỗi khi gọi lệnh tắt nguồn (KHÔNG raise nếu
    lỗi chỉ xảy ra ở bước đọc lại status sau đó — map `OFFLINE`, xem
    `_run_power_command`)."""
    return _run_power_command(printer_id, "off", db_path)

_UPDATE_PRINTER_IS_HELD_SQL = """
UPDATE printers
SET is_held = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

def confirm_printer(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[PrinterResponse]:
    """"Xác nhận vận hành viên" — gỡ `is_held` sau khi operator đã xử lý
    xong máy bị khoá do job `FINISHED`/`ERROR` (E3-4/C3, AC gốc E3-4,
    D-010 điểm 6 — xem `docs/State_E3-4_v2.md` mục "Quyết định phạm
    vi" #6 cho rationale đầy đủ, không lặp lại ở đây).

    Trả `None` nếu không tìm thấy `printer_id` (router map 404). Raise
    `PrinterNotHeldError` nếu máy hiện `is_held = 0` (không có gì để
    xác nhận, router map 409). Nếu `is_held = 1`: UPDATE về `0`
    (KHÔNG đụng cột `status` — chỉ gỡ hold, không đổi trạng thái máy,
    khác `_run_print_command`/`_run_power_command`), trả
    `PrinterResponse` mới nhất."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(_SELECT_PRINTER_BY_ID_SQL, (printer_id,)).fetchone()
        if row is None:
            return None

        is_held = row[11]
        if not is_held:
            raise PrinterNotHeldError(
                f"Máy id={printer_id} không đang ở trạng thái is_held, "
                "không có gì để xác nhận."
            )

        connection.execute(_UPDATE_PRINTER_IS_HELD_SQL, (0, printer_id))
        connection.commit()
        updated_row = connection.execute(
            _SELECT_PRINTER_BY_ID_SQL, (printer_id,)
        ).fetchone()
    finally:
        connection.close()

    return _row_to_response(updated_row)

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
        power_device_name,
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
        power_device_name=power_device_name,
        status=status,
        is_held=bool(is_held),
        created_at=created_at,
        updated_at=updated_at,
    )
