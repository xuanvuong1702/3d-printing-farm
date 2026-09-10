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
