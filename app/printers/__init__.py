"""
Package `app.printers` — domain "Printer Registry" (E1-1, D-001/D-003/
D-006/D-007/D-012/D-013).

Chunk E1-1/C1: `POST /printers` — đăng ký máy in mới bằng IP + tên,
validate kết nối Moonraker, dò + lưu phiên bản/capabilities (D-007) dùng
cho resolution driver sau này (D-012). Theo đúng quy ước CLAUDE.md "mỗi
domain là 1 package con dưới `app/`" (đã áp dụng từ `app/moonraker/`,
`app/db/`).
"""

from __future__ import annotations
