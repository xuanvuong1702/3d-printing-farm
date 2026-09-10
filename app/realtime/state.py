"""
Data layer cho kênh WebSocket real-time (E2-1/C1) - CHƯA kết nối WS
thật (thuộc C2/C3). Chunk này chỉ định nghĩa:
- `RealtimePrinterState`: kết quả đã map, giữ trong bộ nhớ cho 1 máy.
- `RealtimeStateStore`: store in-memory (`printer_id ->
  RealtimePrinterState`), bảo vệ bằng `asyncio.Lock`.
- Hằng số cấu hình: object list subscribe (Quyết định 3) + backoff
  reconnect (Quyết định 2, `docs/State_E2-1_v2.md`).

Quyết định 3 (object set subscribe) - `docs/State_E2-1_v2.md`: HTTP
polling hiện dùng 3 object (`print_stats`, `virtual_sdcard`,
`webhooks` - `app/moonraker/http_client.py::get_status`, không có
nhiệt độ). AC của E2-1 yêu cầu thêm nhiệt độ -> bổ sung `extruder`,
`heater_bed`. Tên field response cho 2 object mới CHƯA được checklist
Moonraker xác nhận trên máy QIDI thật (chỉ 3 object đầu có bằng chứng
từ `print-farm-manager`) - dùng tên field chuẩn Klipper phổ biến
(`extruder.temperature`/`.target`, `heater_bed.temperature`/`.target`),
ghi vào "Rủi ro để lại" của state, xác thực khi có máy QIDI thật (cùng
nhóm D-001/D-003). `SUBSCRIBE_OBJECTS` dùng đúng định dạng tham số của
`printer.objects.subscribe` (dict object -> None nghĩa là "lấy toàn bộ
field của object đó", cùng quy ước `printer.objects.query` mà
`http_client.py::get_status` đã dùng qua query string rỗng).

Quyết định 2 (backoff reconnect WS) - hằng số RIÊNG cho WS, KHÔNG tái
dùng nguyên giá trị heartbeat của E1-4
(`DEFAULT_HEARTBEAT_INTERVAL_SECONDS=30.0`/`_MULTIPLIER=2.0`/
`_MAX_SECONDS=300.0`, `app/heartbeat/service.py`): 2 cơ chế phục vụ mục
đích khác nhau. Heartbeat là 1 vòng lặp POLLING định kỳ cho máy đang
khoẻ mạnh - khoảng cách 30s giữa 2 lần check là bình thường, backoff
tới tận 300s (5 phút) chấp nhận được vì heartbeat chỉ là "phát hiện mất
kết nối", không phải kênh dữ liệu chính. Ở đây, kênh WS chính là nguồn
dữ liệu real-time độc nhất (Quyết định 4 - không ghi DB, không có tầng
polling dự phòng) - mất kết nối càng lâu, dữ liệu hiển thị cho vận hành
viên càng cũ. Backoff khởi điểm ngắn hơn nhiều (1s) và trần thấp hơn
nhiều (60s, so với 300s của heartbeat) để phục hồi kênh dữ liệu chính
nhanh nhất có thể sau khi mạng ổn định trở lại, đồng thời vẫn tránh
"thundering reconnect" (backoff cấp số nhân, cùng công thức dạng với
E1-4) nếu máy in offline kéo dài.

Quyết định 4 (in-memory, không ghi DB) - lý do bảo vệ bằng
`asyncio.Lock`: mỗi máy chỉ có ĐÚNG 1 writer (task WS của chính máy đó,
thuộc C3), nhưng việc đọc toàn bộ store (`get_all`, dự kiến dùng bởi
endpoint tương lai hiển thị real-time cho nhiều máy cùng lúc) cần duyệt
toàn bộ dict trong khi các writer khác có thể đang chèn/xoá key ở giữa
lượt `await` - `RuntimeError: dictionary changed size during iteration`
là rủi ro thật với `asyncio` (nhiều coroutine cùng chạy trên 1 event
loop, không phải multi-thread, nhưng vẫn xen kẽ tại các điểm `await`).
`asyncio.Lock` loại bỏ hoàn toàn rủi ro này với chi phí không đáng kể
(tối đa vài chục máy, tần suất ghi/đọc không phải hot loop tranh chấp
cao). Không dùng `threading.Lock` vì toàn bộ code chạy trên 1 event
loop `asyncio`, không có thread thật nào khác truy cập store này.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

SUBSCRIBE_OBJECTS: Dict[str, Optional[list]] = {
    "print_stats": None,
    "virtual_sdcard": None,
    "webhooks": None,
    "extruder": None,
    "heater_bed": None,
}

WS_RECONNECT_INITIAL_BACKOFF_SECONDS = 1.0
WS_RECONNECT_BACKOFF_MULTIPLIER = 2.0
WS_RECONNECT_MAX_BACKOFF_SECONDS = 60.0

@dataclass(frozen=True)
class RealtimePrinterState:
    """Kết quả đã map từ notification WS cho 1 máy - KHÔNG phải bản ghi
    DB (cùng tinh thần `PrinterStatus` của `http_client.py`, nhưng thêm
    nhiệt độ vì đây là kênh WS, không phải HTTP polling 3 object).

    - `canonical_status`: map theo D-013, cùng bộ giá trị canonical đã
      dùng ở `http_client.py` (map thực hiện ở C2, khi có notification
      thật để map).
    - `progress_percent`/`time_remaining_seconds`/`filename`: cùng ý
      nghĩa với `PrinterStatus` (`http_client.py`).
    - `extruder_temp`/`extruder_target`/`bed_temp`/`bed_target`: MỚI so
      với `PrinterStatus` - chỉ kênh WS mới có (object `extruder`/
      `heater_bed`, Quyết định 3). Tên field nguồn CHƯA xác thực trên
      máy QIDI thật (xem rủi ro ở docstring module).
    - `updated_at`: ISO-8601 UTC, thời điểm notification gần nhất được
      xử lý (KHÔNG phải thời điểm đọc store - để phân biệt "dữ liệu cũ
      vì máy offline lâu" với "vừa đọc nhưng máy vẫn khoẻ mạnh").
    """

    canonical_status: str
    progress_percent: Optional[int]
    time_remaining_seconds: Optional[int]
    filename: Optional[str]
    extruder_temp: Optional[float]
    extruder_target: Optional[float]
    bed_temp: Optional[float]
    bed_target: Optional[float]
    updated_at: str

class RealtimeStateStore:
    """Store in-memory `printer_id -> RealtimePrinterState`, bảo vệ
    bằng `asyncio.Lock` (xem rationale đầy đủ ở docstring module).

    KHÔNG ghi DB (Quyết định 4) - toàn bộ nội dung mất khi service
    restart, chấp nhận được vì pool WS (C3) tự kết nối + subscribe lại
    ngay lúc `lifespan` startup, dữ liệu mới có ngay lập tức.
    """

    def __init__(self) -> None:
        self._states: Dict[int, RealtimePrinterState] = {}
        self._lock = asyncio.Lock()

    async def set(self, printer_id: int, state: RealtimePrinterState) -> None:
        """Ghi/thay thế state của 1 máy (gọi bởi task WS của chính máy
        đó khi nhận notification mới, thuộc C2/C3)."""
        async with self._lock:
            self._states[printer_id] = state

    async def get(self, printer_id: int) -> Optional[RealtimePrinterState]:
        """Đọc state hiện tại của 1 máy, `None` nếu chưa có notification
        nào (máy chưa từng kết nối được, hoặc pool chưa khởi động)."""
        async with self._lock:
            return self._states.get(printer_id)

    async def get_all(self) -> Dict[int, RealtimePrinterState]:
        """Đọc toàn bộ store - trả về bản sao (dict mới) để caller không
        giữ tham chiếu trực tiếp vào cấu trúc nội bộ đang được khoá."""
        async with self._lock:
            return dict(self._states)

    async def delete(self, printer_id: int) -> None:
        """Xoá state của 1 máy (dự kiến dùng khi task WS của máy đó
        dừng hẳn - chưa có caller thật ở chunk này, thuộc C3/C4)."""
        async with self._lock:
            self._states.pop(printer_id, None)
