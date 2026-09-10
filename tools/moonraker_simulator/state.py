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

Phạm vi E2-1/C5 (chunk này, xem `docs/State_E2-1_v6.md` mục "CHUNK KẾ
TIẾP CẦN CHẠY"): thêm field `extruder`/`heater_bed` NATIVE (nhiệt độ) —
trước đây cố ý KHÔNG có (xem "Phạm vi field" ở trên, viết từ E0-5 khi
kênh WS chưa tồn tại). Kênh WS mới (`app.py`, route `/websocket`) cần
subscribe cả 2 object này (Quyết định 3, `app/realtime/state.py`) nên
simulator phải mô phỏng được — endpoint HTTP `GET
/printer/objects/query` hiện có (E0-5/C3) KHÔNG đổi, vẫn chỉ trả 3
object cũ (`http_client.py::get_status` không đọc nhiệt độ, không cần
sửa call site đã khoá). Giá trị mặc định mô phỏng nhiệt độ phòng
(chưa gia nhiệt, chưa có target) - test tự mutate trực tiếp qua Python
object (cùng cách đã dùng cho `virtual_sdcard_progress`) để mô phỏng
"đang gia nhiệt".
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

    extruder_temperature: float = 25.0
    extruder_target: float = 0.0
    heater_bed_temperature: float = 25.0
    heater_bed_target: float = 0.0
