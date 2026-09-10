"""
Pydantic response cho lớp API giám sát real-time (E2-2/C1) — đọc dữ
liệu từ `RealtimeStateStore` (`app/realtime/state.py`, đã khoá từ E2-1)
kết hợp bảng `printers`, KHÔNG tự thu thập dữ liệu mới (xem
`docs/State_E2-2_v2.md` mục "Đọc lại app/realtime/").

`PrinterRealtimeResponse` KHÔNG phản chiếu 1-1 cột DB như
`PrinterResponse` (`app/printers/schemas.py`) — đây là view tổng hợp
riêng cho mục đích giám sát (nhiệt độ/tiến độ/thời gian còn lại), theo
đúng Quyết định phạm vi #4 (`docs/State_E2-2_v2.md`, chốt tại C0):

- `realtime_connected`: `True` nếu `RealtimeStateStore` (E2-1) đã có
  dữ liệu WS cho máy này (đã từng nhận notification), `False` nếu
  chưa (pool chưa kết nối được, hoặc vừa khởi động) — dashboard dùng
  field này để phân biệt "chưa có dữ liệu" với "có dữ liệu nhưng giá
  trị 0/rỗng thật", KHÔNG được suy luận nhầm từ việc các field nhiệt
  độ/tiến độ là `None`.
- `canonical_status`: khi `realtime_connected=True`, lấy từ
  `RealtimePrinterState.canonical_status` (nguồn WS, D-013, mới/chi
  tiết hơn). Khi `realtime_connected=False`, FALLBACK về cột
  `printers.status` trong DB (nguồn heartbeat/HTTP polling, E1-4) —
  dashboard vẫn cần hiển thị được trạng thái tổng quát dù chưa có dữ
  liệu WS chi tiết, không để trống hẳn.
- 4 field nhiệt độ + `progress_percent`/`time_remaining_seconds`/
  `filename`/`updated_at`: `None` khi `realtime_connected=False` —
  KHÔNG suy đoán/mặc định giá trị nào khác.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

class PrinterRealtimeResponse(BaseModel):
    """Snapshot giám sát real-time của 1 máy — trả bởi cả 3 endpoint
    (`GET /printers/{id}/realtime`, `GET /printers/realtime`,
    `GET /printers/realtime/stream`, C2/C3) để đảm bảo dashboard dùng
    đúng 1 cấu trúc dữ liệu cho cả snapshot lẫn stream."""

    id: int
    name: str
    realtime_connected: bool
    canonical_status: str
    progress_percent: Optional[int] = None
    time_remaining_seconds: Optional[int] = None
    filename: Optional[str] = None
    extruder_temp: Optional[float] = None
    extruder_target: Optional[float] = None
    bed_temp: Optional[float] = None
    bed_target: Optional[float] = None
    updated_at: Optional[str] = None
