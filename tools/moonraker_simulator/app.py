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
  - `GET /server/info`, `GET /printer/info` — chunk C2 (done).
  - `GET /printer/objects/query` (params `print_stats`/
    `virtual_sdcard`/`webhooks`) — chunk C3 (done).
  - `POST /server/files/upload`, `POST /printer/gcode/script`,
    `POST /printer/print/cancel`, `POST /printer/print/pause`,
    `POST /printer/print/resume` — chunk C4.

Quyết định chunk C2 (xem `docs/Story_E0-5.md` mục "C2"):
- `get_server_info`/`get_printer_info` trong `http_client.py` chỉ trả
  thẳng `resp.json()["result"]`, không đọc field con cụ thể nào —
  simulator được tự do chọn nội dung, miễn đúng **shape** JSON-RPC-style
  `{"result": {...}}` của Moonraker thật (public API spec, không phải
  dữ liệu QIDI thật) và đủ hợp lý để các story sau (D-007, E1-1 capability
  detection) dùng thử được.
- Phần "tĩnh" (version string, hostname, đường dẫn...) đặt thành hằng số
  module-level ngay dưới đây — **không** thêm field mới vào
  `SimulatorState` (đã chốt 5 field ở C1) chỉ vì 2 endpoint này, vì các
  giá trị đó không đổi trong suốt vòng đời 1 tiến trình simulator.
- Phần gắn với trạng thái Klippy (`klippy_connected`/`klippy_state` ở
  `/server/info`, `state`/`state_message` ở `/printer/info`) đọc trực
  tiếp từ `state.webhooks_state` đã có sẵn trong `SimulatorState` — thực
  ra cùng 1 nguồn dữ liệu với field `webhooks.state` mà
  `GET /printer/objects/query` (C3) sẽ trả, để nếu sau này có chunk nào
  đổi `webhooks_state` (mô phỏng Klippy lỗi) thì cả 3 endpoint đọc trạng
  thái Klippy đều nhất quán, không lệch nhau.

Quyết định chunk C3 (xem `docs/Story_E0-5.md` mục "C3"):
- `GET /printer/objects/query` **luôn trả cả 3 object** `print_stats`/
  `virtual_sdcard`/`webhooks`, KHÔNG lọc theo query params thật sự gửi
  lên — đơn giản hoá hợp lý vì `http_client.py::get_status` (dòng
  148-159) luôn truyền đủ cả 3 key rỗng trong mọi lần gọi, không có
  call site nào trong phạm vi story hiện tại gọi thiếu 1 trong 3 key.
  Nếu sau này phát sinh call site khác chỉ cần 1-2 object, đây sẽ là 1
  chunk mở rộng route (thêm lọc theo `request.query_params.keys()`),
  không phải sửa lại quyết định này.
- Đọc trực tiếp 3 field native đã có sẵn trong `SimulatorState` từ C1
  (`print_stats_state`, `print_stats_filename`,
  `print_stats_print_duration`, `virtual_sdcard_progress`,
  `webhooks_state`) — không thêm field mới, không map sang canonical
  D-013 (việc map là trách nhiệm của `http_client.py::get_status`, đọc
  y hệt máy thật).
"""

from __future__ import annotations

from fastapi import FastAPI

from tools.moonraker_simulator.state import SimulatorState

app = FastAPI(title="QIDI Moonraker Simulator")

state = SimulatorState()

_SIMULATED_MOONRAKER_VERSION = "v0.9.3-simulated"
_SIMULATED_API_VERSION = [1, 0, 0]
_SIMULATED_API_VERSION_STRING = "1.0.0"
_SIMULATED_KLIPPER_VERSION = "v0.12.0-simulated"
_SIMULATED_HOSTNAME = "qidi-moonraker-simulator"
_SIMULATED_MOONRAKER_CONFIG_FILE = "/home/pi/printer_data/config/moonraker.conf"
_SIMULATED_KLIPPER_CONFIG_FILE = "/home/pi/printer_data/config/printer.cfg"

@app.get("/server/info")
def server_info() -> dict:
    """
    GET /server/info — khớp `http_client.py::get_server_info`
    (đọc thẳng `resp.json()["result"]`, không đọc field con cụ thể).

    Shape đúng theo Moonraker thật (JSON-RPC-style envelope
    `{"result": {...}}`), giá trị tĩnh mô phỏng trừ `klippy_connected`/
    `klippy_state` lấy từ `state.webhooks_state` (xem docstring module).
    """
    return {
        "result": {
            "klippy_connected": state.webhooks_state == "ready",
            "klippy_state": state.webhooks_state,
            "components": ["klippy_connection", "file_manager", "job_queue"],
            "failed_components": [],
            "registered_directories": ["gcodes", "config", "logs"],
            "warnings": [],
            "websocket_count": 0,
            "moonraker_version": _SIMULATED_MOONRAKER_VERSION,
            "api_version": _SIMULATED_API_VERSION,
            "api_version_string": _SIMULATED_API_VERSION_STRING,
            "config_file": _SIMULATED_MOONRAKER_CONFIG_FILE,
        }
    }

@app.get("/printer/info")
def printer_info() -> dict:
    """
    GET /printer/info — khớp `http_client.py::get_printer_info`
    (đọc thẳng `resp.json()["result"]`, dùng cho capability detection
    D-007 ở E1-1 sau này).

    `state` dùng đúng field `webhooks_state` (native) — KHÔNG map sang
    canonical D-013 ở đây (đúng quyết định #2 của C1: mọi việc map là
    trách nhiệm của `http_client.py`, simulator chỉ trả native).
    """
    return {
        "result": {
            "state": state.webhooks_state,
            "state_message": (
                "Printer is ready"
                if state.webhooks_state == "ready"
                else f"Klippy state: {state.webhooks_state}"
            ),
            "hostname": _SIMULATED_HOSTNAME,
            "software_version": _SIMULATED_KLIPPER_VERSION,
            "cpu_info": "Simulated CPU - QIDI Moonraker Simulator",
            "klipper_path": "/home/pi/klipper",
            "python_path": "/home/pi/klippy-env/bin/python",
            "log_file": "/home/pi/printer_data/logs/klippy.log",
            "config_file": _SIMULATED_KLIPPER_CONFIG_FILE,
        }
    }

@app.get("/printer/objects/query")
def printer_objects_query() -> dict:
    """
    GET /printer/objects/query — khớp `http_client.py::get_status`
    (dòng 136-197): đọc `resp.json()["result"]["status"]`, rồi lấy
    `status.get("print_stats", {})`, `status.get("virtual_sdcard", {})`,
    `status.get("webhooks", {}).get("state")`.

    Luôn trả cả 3 object bất kể query params thật sự gửi lên (quyết
    định C3, xem docstring module) — `http_client.py` luôn truyền đủ cả
    3 key rỗng nên không cần lọc ở simulator.

    Đọc trực tiếp field native hiện có trong `SimulatorState` (không map
    canonical D-013 — đúng quyết định #2 của C1).
    """
    return {
        "result": {
            "status": {
                "print_stats": {
                    "state": state.print_stats_state,
                    "filename": state.print_stats_filename,
                    "print_duration": state.print_stats_print_duration,
                },
                "virtual_sdcard": {
                    "progress": state.virtual_sdcard_progress,
                },
                "webhooks": {
                    "state": state.webhooks_state,
                },
            }
        }
    }
