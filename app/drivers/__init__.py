"""
Package `app.drivers` — lớp Driver/Adapter theo model/firmware (E0-6, D-006).

Tầng nghiệp vụ (monitoring, control, queue) chỉ được import từ package
này (`PrinterDriver`, `BaseKlipperDriver`, và sau này `resolve_driver`
ở chunk C2) — không import trực tiếp `app.moonraker.http_client` ở tầng
service, để giữ đúng ranh giới "chỉ gọi qua interface driver chung" (AC
gốc E0-6, `Backlog.md`).
"""

from __future__ import annotations

from app.drivers.base import BaseKlipperDriver, PrinterDriver

__all__ = ["PrinterDriver", "BaseKlipperDriver"]
