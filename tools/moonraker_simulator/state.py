"""
State machine nội bộ của Moonraker simulator (tools/moonraker_simulator).

Quyết định áp dụng (chốt tại E0-5/C1, xem docs/Story_E0-5.md):
- Lưu đúng field NATIVE Klipper/Moonraker (`print_stats.state`,
  `print_stats.filename`, `print_stats.print_duration`,
  `virtual_sdcard.progress`, `webhooks.state`) — KHÔNG lưu canonical
  status D-013 trực tiếp trong state machine. Việc map sang canonical
  là trách nhiệm của `app/moonraker/http_client.py::get_status` khi gọi
  vào simulator, y hệt cách nó xử lý máy thật — đúng mục đích "giả
  lập", không phải "trả sẵn kết quả đã map".
- Phạm vi field: đúng những field mà `http_client.py` đọc (đối chiếu
  D-002 phần (1) — xem docstring từng hàm trong file đó qua
  `GET /printer/objects/query`), không thêm field Klipper khác không
  dùng tới (vd. `extruder`, `heater_bed`, `toolhead`...).
- 1 tiến trình simulator = state của đúng 1 máy in giả lập (khớp cách
  `http_client.py` gọi theo host/port riêng của từng máy — chạy nhiều
  máy giả lập nghĩa là chạy nhiều tiến trình simulator ở cổng khác
  nhau, không phải 1 state machine phục vụ nhiều máy).

Logic khởi tạo lại trạng thái khi upload, chuyển trạng thái khi
cancel/pause/resume, và mô phỏng tăng `print_duration`/`progress` theo
thời gian đều thuộc phạm vi các chunk sau (C2 đọc info tĩnh, C3 đọc
`objects/query`, C4 các endpoint điều khiển) — CHƯA implement ở đây;
chunk C1 chỉ chốt cấu trúc dữ liệu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

@dataclass
class SimulatorState:
    """Trạng thái nội bộ của 1 máy in giả lập, dùng field native Moonraker/Klipper."""

    webhooks_state: str = "ready"

    print_stats_state: str = "standby"
    print_stats_filename: Optional[str] = None
    print_stats_print_duration: float = 0.0

    virtual_sdcard_progress: float = 0.0
