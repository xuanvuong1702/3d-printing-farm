"""
FastAPI app của Moonraker simulator (tools/moonraker_simulator).

Quyết định kiến trúc (chốt tại E0-5/C1, xem docs/Story_E0-5.md):
- Chạy như 1 tiến trình HTTP riêng (FastAPI + Uvicorn, dependency đã có
  sẵn từ E0-1) lắng nghe cổng giống Moonraker thật (mặc định 7125,
  D-001) — để `app/moonraker/http_client.py` (E0-3) gọi thẳng vào bằng
  `host`/`port` trỏ tới simulator, KHÔNG cần sửa `http_client.py`.
- Vị trí `tools/moonraker_simulator/` (ngoài `app/`) — đây là dev
  tooling/test fixture, không phải thành phần service production; xem
  `CLAUDE.md` mục "Quy ước code".
- 8 endpoint cần mô phỏng, khớp chính xác từng hàm gọi trong
  `app/moonraker/http_client.py` (D-002 phần (1)):
  - `GET /server/info`, `GET /printer/info` — chunk C2.
  - `GET /printer/objects/query` (params `print_stats`/
    `virtual_sdcard`/`webhooks`) — chunk C3.
  - `POST /server/files/upload`, `POST /printer/gcode/script`,
    `POST /printer/print/cancel`, `POST /printer/print/pause`,
    `POST /printer/print/resume` — chunk C4.

Route thật CHƯA được thêm ở chunk C1 này — chỉ dựng khung app + gắn
`SimulatorState` dùng chung cho các chunk sau.
"""

from __future__ import annotations

from fastapi import FastAPI

from tools.moonraker_simulator.state import SimulatorState

app = FastAPI(title="QIDI Moonraker Simulator")

state = SimulatorState()
