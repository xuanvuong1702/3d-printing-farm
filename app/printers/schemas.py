"""
Pydantic request/response cho `POST /printers` (E1-1/C1).

Quyết định áp dụng (xem `docs/Decisions.md` để biết nội dung đầy đủ,
không copy lại ở đây):
- D-001: `moonraker_port` mặc định `DEFAULT_MOONRAKER_PORT` (7125), lưu
  theo từng máy (không hard-code toàn hệ thống).
- D-003: `api_key` tuỳ chọn (nullable) — không phải máy QIDI nào cũng
  bắt buộc bật `[authorization]`.
- D-007: `moonraker_version`/`klipper_version`/`capabilities` là dữ
  liệu capability detection dò được lúc đăng ký, không phải input của
  request — chỉ xuất hiện ở `PrinterResponse`, không có trong
  `PrinterCreateRequest`.

`EmergencyStopRequest` (E3-2/C3) — xem docstring class bên dưới, đúng
1 field `confirm: bool = False` (fail-safe).

`PrinterResponse` phản chiếu đúng các cột của bảng `printers`
(`app/db/schema.py::CREATE_PRINTERS_SQL`), trừ việc giải mã cột
`capabilities` (JSON TEXT trong DB) thành `List[str]` cho tiện dùng ở
tầng client, và `is_held` (INTEGER 0/1 trong DB) thành `bool`.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT

class PrinterCreateRequest(BaseModel):
    """Body của `POST /printers` — đúng 5 field theo AC gốc E1-1."""

    name: str
    ip: str
    moonraker_port: int = Field(default=DEFAULT_MOONRAKER_PORT)
    model: Optional[str] = None
    api_key: Optional[str] = None

class PrinterUpdateRequest(BaseModel):
    """Body của `PATCH /printers/{printer_id}` (E1-3/C1) — chỉ 3 field
    được phép sửa: `name`/`model`/`api_key` (xem `docs/State_E1-3_v2.md`
    mục "Quyết định phạm vi chốt tại chunk C0", điểm 2). KHÔNG cho sửa
    `ip`/`moonraker_port` (khoá định danh máy, đổi = "trỏ sang máy vật
    lý khác", ngoài phạm vi story) hay các field do service/driver tự
    cập nhật (`moonraker_version`/`klipper_version`/`capabilities`/
    `status`/`is_held`).

    Tầng service dùng `model_dump(exclude_unset=True)` để phân biệt
    "không truyền field" (giữ nguyên) với "truyền `null` tường minh"
    (xoá giá trị hiện có về `NULL` — áp dụng được vì `model`/`api_key`
    đều nullable ở DDL, `app/db/schema.py`).
    """

    name: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None

class EmergencyStopRequest(BaseModel):
    """Body của `POST /printers/{printer_id}/emergency_stop` (E3-2/C3) —
    đúng 1 field `confirm`, mặc định `False` (fail-safe). Route chỉ
    thực thi lệnh khi `confirm=true`; thiếu field hoặc `confirm=false`
    → `422 Unprocessable Entity` (xem `docs/State_E3-2_v4.md` mục
    "Quyết định phạm vi" #5). Đáp ứng phần "có xác nhận trước khi gửi"
    của AC gốc E3-2 ở tầng API — UI xác nhận thật (dialog) thuộc phạm
    vi Frontend Dashboard (Epic 9, ngoài phạm vi service backend)."""

    confirm: bool = False

class PrinterResponse(BaseModel):
    """Phản chiếu 1 dòng của bảng `printers` sau khi đăng ký thành công."""

    id: int
    name: str
    ip: str
    moonraker_port: int
    model: Optional[str] = None
    api_key: Optional[str] = None
    moonraker_version: Optional[str] = None
    klipper_version: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    status: str
    is_held: bool
    created_at: str
    updated_at: str
