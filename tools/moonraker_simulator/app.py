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
    `POST /printer/print/resume` — chunk C4 (done).

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

Quyết định chunk C4 (xem `docs/Story_E0-5.md` mục "C4"):
- **Không mô phỏng progress/print_duration tự tăng theo thời gian
  thực** (không dùng background thread/task). Cả 5 endpoint chỉ
  **mutate `state` một lần khi nhận request**, set giá trị tĩnh hợp lý
  (vd. upload → `progress = 0.0`). AC gốc chỉ yêu cầu "dev/test được
  toàn bộ luồng API mà không phụ thuộc máy in vật lý" — không yêu cầu
  mô phỏng tốc độ in thật. Khi C5 cần quan sát "progress đang tăng",
  test sẽ tự mutate `state.virtual_sdcard_progress`/
  `state.print_stats_print_duration` trực tiếp qua Python object giữa
  các bước (cùng cách đã dùng để kiểm chứng C3 — chạy simulator qua
  `uvicorn.Server` cùng tiến trình để có quyền truy cập instance
  `state`), thay vì chờ simulator tự tăng. Nếu sau này phát sinh nhu
  cầu thật cần mô phỏng tốc độ in (vd. demo trực quan), đó là 1 chunk
  mở rộng riêng — không mặc định làm ở đây.
- **Không validate thứ tự chuyển trạng thái** — mỗi endpoint set thẳng
  `print_stats_state` theo đúng lệnh nhận được, bất kể trạng thái hiện
  tại (vd. `pause` khi đang `standby` vẫn set `paused`, không trả lỗi).
  Đơn giản hoá hợp lý cho mục đích dev/test luồng API; nghiệp vụ thật
  (vd. chặn pause khi không có job đang chạy) thuộc tầng service, không
  phải trách nhiệm của simulator giả lập hành vi Moonraker/Klipper.
- **`POST /server/files/upload` giữ đúng "bug thật" đã ghi trong
  checklist Moonraker** (Decisions.md, dòng 236): chỉ bắt đầu in
  (`print_stats_state = "printing"`) khi field `print` được gửi đúng
  dạng **multipart form field** giá trị `"true"` — nếu thiếu hoặc gửi
  sai vị trí (vd. query param), simulator **âm thầm bỏ qua**, vẫn trả
  `200` nhưng không đổi state, y hệt hành vi Moonraker thật đã ghi
  nhận. Việc này giúp C5 (test tích hợp) có thể viết 1 test case xác
  nhận `http_client.py::upload_and_print` (đã code đúng, gửi `print`
  qua `data=` chứ không phải `params=`) không dính bug đó.

Quyết định chunk C5 của E2-1 (`docs/State_E2-1_v6.md` mục "CHUNK KẾ
TIẾP CẦN CHẠY", Quyết định 8) — thêm route `WebSocket /websocket`, MỞ
RỘNG simulator hiện có thay vì tạo hạ tầng test riêng:
- Giao thức: đúng subset JSON-RPC 2.0 mà `moonraker-api` (thư viện,
  `app/realtime/connection.py`) dùng thật — xác nhận qua đọc trực tiếp
  `moonraker_api/websockets/websocketclient.py`
  (`_build_websocket_request`/`loop_recv`): request `{"jsonrpc":"2.0",
  "method":..., "id":..., "params": {...}}`, response khớp lại theo
  `id` (`{"id":..., "result": {...}}`), notification không có `id`
  (`{"method": "notify_status_update", "params": [delta, eventtime]}`).
  Chỉ implement ĐÚNG method mà `PrinterWebsocketConnection.connect()`
  gọi (`printer.objects.subscribe`) — method khác (ngoài phạm vi story)
  vẫn được trả `{"id":..., "result": {}}` để không làm client treo chờ
  response mãi, không phải trả lỗi (đơn giản hoá hợp lý, cùng tinh thần
  "không validate nghiệp vụ" đã áp dụng cho các route điều khiển ở C4).
- Snapshot lúc subscribe: đọc `_websocket_status_snapshot()` bên dưới —
  TÁI DÙNG 3 field native đã có (`print_stats`/`virtual_sdcard`/
  `webhooks`, giống hệt `GET /printer/objects/query`) + 2 field MỚI
  (`extruder`/`heater_bed`, thêm ở `state.py` chunk này) — chỉ trả đúng
  object nào có trong `params.objects` của request thật (khác
  `GET /printer/objects/query` HTTP, vốn luôn trả cả 3 bất kể query —
  quyết định C3 cũ, KHÔNG áp dụng ở đây vì `SUBSCRIBE_OBJECTS`,
  `app/realtime/state.py`, luôn gửi đủ cả 5 nên không có call site nào
  cần lọc thật sự, nhưng lọc đúng theo params vẫn rẻ và đúng tinh thần
  giả lập hơn).
- Notification định kỳ mô phỏng thay đổi: 1 task nền/kết nối, poll
  `state` mỗi `_WS_BROADCAST_POLL_INTERVAL_SECONDS` (0.05s — cùng chu kỳ
  poll ngắn đã dùng cho khởi động simulator ở `tests/moonraker_simulator/
  test_integration.py`/`tests/heartbeat/conftest.py`, đủ nhanh cho test,
  không phải mô phỏng tốc độ đổi nhiệt độ thật của máy in), so sánh với
  snapshot đã gửi lần trước — CHỈ gửi object nào THẬT SỰ đổi giá trị
  (đúng hành vi delta thật của Moonraker mà `PrinterWebsocketConnection.
  _handle_status_delta()` — `app/realtime/connection.py`, C2 — đã viết
  logic merge tích luỹ để xử lý). Test tự mutate `state` (module-level,
  cùng cách `tests/moonraker_simulator/test_integration.py` đã làm cho
  `virtual_sdcard_progress`) giữa các lần đợi vòng lặp này để mô phỏng
  "máy đang đổi trạng thái", KHÔNG cần simulator tự tăng nhiệt độ/tiến
  độ theo thời gian thực (cùng quyết định C4 cũ, không mô phỏng tốc độ
  in thật).
- Đóng task nền khi client ngắt kết nối (`WebSocketDisconnect`) hoặc
  server chủ động đóng — tránh rò rỉ task nếu test mở/đóng nhiều kết
  nối liên tiếp (nhiều máy giả lập trong 1 tiến trình test).

Quyết định chunk C2 của E3-2 (`docs/State_E3-2_v3.md` mục "CHUNK KẾ
TIẾP CẦN CHẠY", "Quyết định phạm vi" #2/#3/#11) — thêm route `POST
/printer/emergency_stop`, khớp `http_client.py::emergency_stop`
(E3-2/C1): set `state.webhooks_state = "shutdown"`, KHÔNG đổi
`print_stats_state` (không cần thiết, xem rationale đầy đủ ở state
file trên) — tái dùng đúng field native `webhooks_state` đã có từ
E0-5/C1, không thêm field mới vào `SimulatorState`.

Quyết định chunk C2 của E3-3 (`docs/State_E3-3_v3.md` mục "CHUNK KẾ
TIẾP CẦN CHẠY", `docs/State_E3-3_v2.md` "Quyết định phạm vi" #2/#11) —
mô phỏng Machine/Power API, khớp
`http_client.py::set_device_power` (E3-3/C1):
- Thêm field mới `power_devices: dict[str, str]` vào `SimulatorState`
  (`state.py`, E3-3/C2) — khác toàn bộ field hiện có (không phải 1 giá
  trị scalar cố định của 1 máy, mà 1 dict nhiều device có thể có trong
  cùng 1 máy), có sẵn 1 device mẫu `"printer"` mặc định `"off"`.
- `POST /machine/device_power/device`: đọc JSON body `{"device",
  "action"}` (Pydantic model `DevicePowerRequest` — style JSON body
  đầu tiên trong file này, khác `gcode_script` dùng query string hay
  `upload_file` dùng multipart form; đây là request khớp đúng shape
  JSON của Moonraker thật, tận dụng validation tự động của FastAPI/
  Pydantic thay vì tự đọc `Request` thô). KHÔNG validate `action` phải
  thuộc `{"on", "off"}` và KHÔNG validate `device` đã tồn tại sẵn
  trong dict — nếu `device` chưa có, tạo mới luôn (đúng tinh thần
  "simulator dev/test, không phải Moonraker thật" đã ghi trong state
  file — validate nghiệp vụ, ví dụ 409 khi máy không hỗ trợ, là trách
  nhiệm tầng `app/printers/service.py` ở C3, không phải simulator).
  Trả về `{"<device>": "<trạng_thái_mới>"}` — đúng shape response thật
  của Machine/Power API (không bọc thêm `"result"` — endpoint này của
  Moonraker thật không dùng envelope JSON-RPC-style như các endpoint
  `/printer/...`/`/server/...` khác, xác nhận qua cùng nguồn tài liệu
  đã ghi ở `http_client.py::set_device_power`).
- `GET /machine/device_power/device?device=<tên>` (tuỳ chọn theo
  "Quyết định phạm vi" #2/#11 — thêm vì rẻ và tiện cho việc viết test
  ở C4 sau này, không bắt buộc dùng): đọc lại trạng thái hiện tại của
  1 device, trả cùng shape response như route POST ở trên. Nếu
  `device` chưa từng được set (không có trong dict) → trả `"off"` mặc
  định (không tạo mới trong dict qua đường đọc, khác đường ghi POST ở
  trên — đọc không có tác dụng phụ).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from tools.moonraker_simulator.state import SimulatorState

class DevicePowerRequest(BaseModel):
    """Body JSON của `POST /machine/device_power/device` (E3-3/C2) — khớp
    đúng 2 field mà `http_client.py::set_device_power` gửi lên, KHÔNG thêm
    field nào khác (Moonraker thật còn hỗ trợ các field khác không dùng tới
    ở phạm vi story này)."""

    device: str
    action: str

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

@app.post("/server/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    print_flag: str | None = Form(None, alias="print"),
) -> dict:
    """
    POST /server/files/upload — khớp `http_client.py::upload_and_print`
    (dòng 232-259): multipart form-data, field `file` + field `print`.

    Giữ đúng "bug thật" đã ghi trong checklist Moonraker (Decisions.md,
    dòng 236, quyết định C4): chỉ bắt đầu in khi `print_flag == "true"`
    **và** nó thật sự đến từ multipart form field (tham số `print_flag`
    dùng `Form(...)`, không phải `Query(...)`, nên nếu client gửi
    `print` qua query string thay vì form field — như bug đã xảy ra ở
    dự án tham khảo — FastAPI sẽ KHÔNG bind được giá trị vào đây,
    `print_flag` vẫn là `None`, và simulator âm thầm bỏ qua đúng như
    Moonraker thật.
    """
    content = await file.read()
    if print_flag == "true":
        state.print_stats_state = "printing"
        state.print_stats_filename = file.filename
        state.print_stats_print_duration = 0.0
        state.virtual_sdcard_progress = 0.0
    return {
        "result": {
            "item": {
                "path": f"gcodes/{file.filename}",
                "root": "gcodes",
                "size": len(content),
            },
            "print_started": print_flag == "true",
        }
    }

@app.post("/printer/gcode/script")
def gcode_script(script: str = "") -> dict:
    """
    POST /printer/gcode/script?script=... — khớp
    `http_client.py::gcode_script` (dòng 200-229), tham số `script`
    truyền qua QUERY STRING (khác `upload_and_print` ở trên).

    Quyết định C4: chạy "thành công" luôn, KHÔNG đổi state — đã xác
    nhận không có script cụ thể nào trong phạm vi 8 hàm client hiện có
    (E0-3 đã đóng) cần simulator xử lý riêng (vd. không có call site
    nào dùng `gcode_script` để tự cancel/pause qua gcode thay vì gọi
    thẳng endpoint tương ứng).
    """
    return {"result": "ok"}

@app.post("/printer/print/cancel")
def cancel_print() -> dict:
    """POST /printer/print/cancel — khớp `http_client.py::cancel_job`.

    Set thẳng `print_stats_state = "cancelled"` (map sang canonical
    `STOPPED` ở tầng `http_client.py` qua `_PRINT_STATS_STATE_MAP`,
    simulator chỉ set giá trị native) — không validate trạng thái hiện
    tại trước đó (quyết định C4, xem docstring module).
    """
    state.print_stats_state = "cancelled"
    return {"result": "ok"}

@app.post("/printer/print/pause")
def pause_print() -> dict:
    """POST /printer/print/pause — khớp `http_client.py::pause_job`.

    Set thẳng `print_stats_state = "paused"`, không validate (quyết
    định C4).
    """
    state.print_stats_state = "paused"
    return {"result": "ok"}

@app.post("/printer/print/resume")
def resume_print() -> dict:
    """POST /printer/print/resume — khớp `http_client.py::resume_job`.

    Set thẳng `print_stats_state = "printing"`, không validate (quyết
    định C4).
    """
    state.print_stats_state = "printing"
    return {"result": "ok"}

@app.post("/printer/emergency_stop")
def emergency_stop() -> dict:
    """POST /printer/emergency_stop — khớp `http_client.py::emergency_stop`
    (E3-2/C1).

    Mô phỏng đúng hành vi Moonraker thật: đưa Klippy vào trạng thái
    "shutdown" (`webhooks.state != 'ready'`), khiến `get_status()` map
    ngay về `OFFLINE` (D-013) ở lần gọi kế tiếp — KHÔNG đổi
    `print_stats_state` (không cần thiết, xem `docs/State_E3-2_v3.md`
    "Quyết định phạm vi" #11: `get_status()` map OFFLINE ngay khi
    `webhooks.state != 'ready'`, bất kể `print_stats.state`).
    """
    state.webhooks_state = "shutdown"
    return {"result": "ok"}

@app.post("/machine/device_power/device")
def set_device_power(body: DevicePowerRequest) -> dict:
    """POST /machine/device_power/device — khớp
    `http_client.py::set_device_power` (E3-3/C1).

    Cập nhật `state.power_devices[body.device] = body.action` (tạo mới
    key nếu `device` chưa từng tồn tại), không validate `action`/`device`
    (quyết định C2, xem docstring module) — trả về đúng shape response
    thật `{"<device>": "<trạng_thái_mới>"}`, KHÔNG bọc `"result"`.
    """
    state.power_devices[body.device] = body.action
    return {body.device: state.power_devices[body.device]}

@app.get("/machine/device_power/device")
def get_device_power(device: str) -> dict:
    """GET /machine/device_power/device?device=<tên> — tuỳ chọn (quyết định
    C2, không bắt buộc theo "Quyết định phạm vi" #2/#11), phục vụ test/dev
    đọc lại trạng thái hiện tại của 1 device.

    Không có tác dụng phụ: nếu `device` chưa từng được set qua route POST ở
    trên, trả mặc định `"off"` mà KHÔNG tạo key mới trong
    `state.power_devices` (khác route POST).
    """
    return {device: state.power_devices.get(device, "off")}

_WS_BROADCAST_POLL_INTERVAL_SECONDS = 0.05

def _websocket_status_snapshot(objects: List[str]) -> Dict[str, Dict[str, Any]]:
    """Đọc `state` hiện hành, trả đúng các object có trong `objects`
    (tham số của `printer.objects.subscribe` thật) - dùng chung cho cả
    response subscribe ban đầu lẫn vòng lặp broadcast bên dưới."""
    full: Dict[str, Dict[str, Any]] = {
        "print_stats": {
            "state": state.print_stats_state,
            "filename": state.print_stats_filename,
            "print_duration": state.print_stats_print_duration,
        },
        "virtual_sdcard": {"progress": state.virtual_sdcard_progress},
        "webhooks": {"state": state.webhooks_state},
        "extruder": {
            "temperature": state.extruder_temperature,
            "target": state.extruder_target,
        },
        "heater_bed": {
            "temperature": state.heater_bed_temperature,
            "target": state.heater_bed_target,
        },
    }
    return {name: full[name] for name in objects if name in full}

@app.websocket("/websocket")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Mô phỏng kênh WS Moonraker thật cho ĐÚNG 1 client (đủ cho
    `PrinterWebsocketConnection`, `app/realtime/connection.py`, C2/C5 —
    Moonraker thật cho phép nhiều client, simulator không cần vì mỗi
    máy giả lập chỉ có 1 kết nối/lượt test).

    Chỉ implement `printer.objects.subscribe` (method duy nhất mà
    `PrinterWebsocketConnection.connect()` gọi) — method khác trả
    `result` rỗng để không treo client (xem docstring module)."""
    await websocket.accept()
    subscribed_objects: Optional[List[str]] = None
    last_snapshot: Dict[str, Dict[str, Any]] = {}
    broadcaster_task: Optional["asyncio.Task[None]"] = None

    async def _broadcast_loop() -> None:
        nonlocal last_snapshot
        while True:
            await asyncio.sleep(_WS_BROADCAST_POLL_INTERVAL_SECONDS)
            if not subscribed_objects:
                continue
            current = _websocket_status_snapshot(subscribed_objects)
            delta = {
                name: value
                for name, value in current.items()
                if value != last_snapshot.get(name)
            }
            if delta:
                last_snapshot = current
                await websocket.send_json(
                    {
                        "jsonrpc": "2.0",
                        "method": "notify_status_update",
                        "params": [delta, time.time()],
                    }
                )

    try:
        while True:
            message = await websocket.receive_json()
            method = message.get("method")
            req_id = message.get("id")
            if method == "printer.objects.subscribe":
                params = message.get("params") or {}
                requested_objects = list((params.get("objects") or {}).keys())
                subscribed_objects = requested_objects
                last_snapshot = _websocket_status_snapshot(requested_objects)
                if broadcaster_task is None:
                    broadcaster_task = asyncio.create_task(_broadcast_loop())
                await websocket.send_json(
                    {
                        "id": req_id,
                        "result": {
                            "eventtime": time.time(),
                            "status": last_snapshot,
                        },
                    }
                )
            elif req_id is not None:

                await websocket.send_json({"id": req_id, "result": {}})
    except WebSocketDisconnect:
        pass
    finally:
        if broadcaster_task is not None:
            broadcaster_task.cancel()
            try:
                await broadcaster_task
            except asyncio.CancelledError:
                pass
