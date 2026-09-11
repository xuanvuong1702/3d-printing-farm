"""
Package `app.heartbeat` - domain "Heartbeat/Reconnect Detection" (E1-4).

Chunk E1-4/C1 (chunk này): chỉ định nghĩa hằng số cấu hình (interval mặc
định, backoff) trong `service.py` - CHƯA có logic chạy heartbeat thật
(thuộc C2) hay wiring vào FastAPI lifespan (thuộc C3, `scheduler.py`).
Theo đúng quy ước CLAUDE.md "mỗi domain là 1 package con dưới `app/`".
"""

from __future__ import annotations
