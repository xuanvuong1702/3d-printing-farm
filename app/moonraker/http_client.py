"""
Lớp gọi lệnh rời rạc (request/response) tới Moonraker qua httpx thuần.

Quyết định áp dụng:
- D-002 phần (1): gọi thẳng bằng httpx, KHÔNG dùng thư viện client trung
  gian. Endpoint/field/công thức lấy từ "Phụ lục: Checklist kỹ thuật
  Moonraker" trong Decisions.md (đã kiểm chứng qua print-farm-manager).
- D-013: canonical status cho printers.status / jobs.status.
- CLAUDE.md nguyên tắc #2: lớp này KHÔNG chạm DB — chỉ gọi Moonraker và
  trả về dữ liệu đã map sang canonical; tầng service (chunk sau) chịu
  trách nhiệm ghi DB.
- CLAUDE.md nguyên tắc #4: gặp trạng thái native lạ -> map về "UNKNOWN",
  không tự bịa giá trị mới.

Phạm vi E0-3 (gộp qua các chunk C1/C1b/gap-fill tại C5): server_info/
printer_info, đọc trạng thái máy, tính progress/timeRemaining, lấy tên
file đang in, chạy gcode tuỳ ý (`gcode_script`), upload G-code + in ngay,
cancel/pause/resume job, checkIfPrinting. KHÔNG bao gồm: kênh WebSocket
(D-002 phần 2 / Q-001 - đã chốt dùng `moonraker-api`, nhưng việc tích hợp
thư viện đó thuộc phạm vi story khác, vd. E2-1).

Phạm vi E3-2/C1: `emergency_stop`. Phạm vi E3-3/C1: `set_device_power` -
bật/tắt smart plug/device qua Machine/Power API (xem docstring hàm đó).

Phạm vi E4-1/C1 (chunk này): `upload_file` (upload KHÔNG in ngay, khác
`upload_and_print`) + `get_file_metadata` (kích thước/thời gian in ước
tính) - xem docstring từng hàm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

DEFAULT_MOONRAKER_PORT = 7125

INFO_TIMEOUT_SECONDS = 8.0
UPLOAD_TIMEOUT_SECONDS = 120.0

CANONICAL_IDLE = "IDLE"
CANONICAL_PRINTING = "PRINTING"
CANONICAL_PAUSED = "PAUSED"
CANONICAL_FINISHED = "FINISHED"
CANONICAL_STOPPED = "STOPPED"
CANONICAL_ERROR = "ERROR"
CANONICAL_OFFLINE = "OFFLINE"
CANONICAL_UNKNOWN = "UNKNOWN"

_PRINT_STATS_STATE_MAP = {
    "standby": CANONICAL_IDLE,
    "printing": CANONICAL_PRINTING,
    "paused": CANONICAL_PAUSED,
    "complete": CANONICAL_FINISHED,
    "error": CANONICAL_ERROR,
    "cancelled": CANONICAL_STOPPED,
}

ACTIVE_JOB_STATUSES = {CANONICAL_PRINTING, CANONICAL_PAUSED}

class MoonrakerClientError(Exception):
    """Lỗi chung khi gọi Moonraker (network, timeout, HTTP status xấu)."""

@dataclass
class PrinterStatus:
    """Kết quả đã map sang canonical - KHÔNG phải bản ghi DB."""

    canonical_status: str
    raw_print_stats_state: Optional[str]
    progress_percent: Optional[int]
    time_remaining_seconds: Optional[int]
    filename: Optional[str]

def _base_url(host: str, port: int = DEFAULT_MOONRAKER_PORT) -> str:
    return f"http://{host}:{port}"

def _request(
    method: str,
    host: str,
    path: str,
    *,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
    timeout: float = INFO_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> httpx.Response:
    """Gọi HTTP tới Moonraker, không dùng thư viện trung gian (D-002 phần 1)."""
    headers = kwargs.pop("headers", {}) or {}
    if api_key:

        headers["X-Api-Key"] = api_key
    url = f"{_base_url(host, port)}{path}"
    try:
        response = httpx.request(
            method, url, headers=headers, timeout=timeout, **kwargs
        )
    except httpx.RequestError as exc:
        raise MoonrakerClientError(
            f"Không gọi được Moonraker tại {url}: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise MoonrakerClientError(
            f"Moonraker trả lỗi {response.status_code} cho {url}: {response.text}"
        )
    return response

def get_server_info(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    """GET /server/info - dùng ở E0-2 để xác nhận cổng/API, và làm health-check."""
    resp = _request("GET", host, "/server/info", port=port, api_key=api_key)
    return resp.json()["result"]

def get_printer_info(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    """GET /printer/info - phiên bản Moonraker/Klipper, dùng cho capability detection (D-007)."""
    resp = _request("GET", host, "/printer/info", port=port, api_key=api_key)
    return resp.json()["result"]

def _map_print_stats_state(raw_state: Optional[str]) -> str:
    """Map print_stats.state -> canonical. Trạng thái lạ -> UNKNOWN (nguyên tắc #4 CLAUDE.md)."""
    if raw_state is None:
        return CANONICAL_UNKNOWN
    return _PRINT_STATS_STATE_MAP.get(raw_state, CANONICAL_UNKNOWN)

def get_status(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> PrinterStatus:
    """
    GET /printer/objects/query với print_stats/virtual_sdcard/webhooks.

    Quy tắc map (Phụ lục Checklist Moonraker):
    - print_stats.state -> canonical qua _PRINT_STATS_STATE_MAP.
    - Bất kể print_stats.state là gì, nếu webhooks.state != 'ready' thì
      LUÔN trả OFFLINE (Klippy chưa sẵn sàng).
    - progress/timeRemaining chỉ tính khi virtual_sdcard.progress > 0.02.
    """
    resp = _request(
        "GET",
        host,
        "/printer/objects/query",
        port=port,
        api_key=api_key,
        params={
            "print_stats": "",
            "virtual_sdcard": "",
            "webhooks": "",
        },
    )
    status = resp.json()["result"]["status"]

    webhooks_state = status.get("webhooks", {}).get("state")
    print_stats = status.get("print_stats", {})
    virtual_sdcard = status.get("virtual_sdcard", {})

    raw_state = print_stats.get("state")

    if webhooks_state != "ready":
        canonical_status = CANONICAL_OFFLINE
    else:
        canonical_status = _map_print_stats_state(raw_state)

    pct = virtual_sdcard.get("progress")
    elapsed = print_stats.get("print_duration")

    progress_percent: Optional[int] = None
    time_remaining_seconds: Optional[int] = None
    if pct is not None:

        if pct > 0.02:
            progress_percent = round(pct * 100)
            if elapsed is not None:
                time_remaining_seconds = round(elapsed * (1 - pct) / pct)
        else:
            progress_percent = round(pct * 100)

    filename = None
    if canonical_status in (CANONICAL_PRINTING, CANONICAL_PAUSED):
        filename = print_stats.get("filename")

    return PrinterStatus(
        canonical_status=canonical_status,
        raw_print_stats_state=raw_state,
        progress_percent=progress_percent,
        time_remaining_seconds=time_remaining_seconds,
        filename=filename,
    )

def gcode_script(
    host: str,
    script: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    """
    POST /printer/gcode/script?script=<script> - chạy một dòng/script gcode
    tuỳ ý (AC gốc của E0-3 liệt kê `gcode_script` là 1 trong các lệnh rời
    rạc bắt buộc, tách biệt với cancel/pause/resume/upload).

    Xác nhận nguồn (chunk C5/E0-3, KHÔNG nằm trong "Phụ lục: Checklist kỹ
    thuật Moonraker" tham chiếu từ print-farm-manager - dự án đó không gọi
    endpoint này): tài liệu Moonraker chính thức
    (moonraker.readthedocs.io/en/latest/web_api/, mục "GCode APIs") - ví
    dụ minh hoạ chính thức là `POST /printer/gcode/script?script=G28`,
    tham số `script` truyền qua QUERY STRING (khác với `upload_and_print`,
    nơi field `print` bắt buộc là multipart form field - hai endpoint có
    quy ước tham số khác nhau, không suy diễn cùng 1 kiểu cho cả hai).
    Trả lời thành công khi gcode đã chạy xong (không phải chỉ xếp hàng).
    """
    resp = _request(
        "POST",
        host,
        "/printer/gcode/script",
        port=port,
        api_key=api_key,
        params={"script": script},
    )
    return resp.json()

def upload_and_print(
    host: str,
    filename: str,
    file_content: bytes,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    """
    POST /server/files/upload, multipart form-data.

    CẢNH BÁO (bug thật đã xảy ra ở dự án tham khảo, ghi trong checklist):
    field `print` PHẢI là multipart form field với giá trị "true" (string),
    KHÔNG được đưa vào query string. Nếu sai, Moonraker âm thầm bỏ qua và
    trả 200 nhưng không in gì cả.
    """
    files = {"file": (filename, file_content)}
    data = {"print": "true"}
    resp = _request(
        "POST",
        host,
        "/server/files/upload",
        port=port,
        api_key=api_key,
        timeout=UPLOAD_TIMEOUT_SECONDS,
        files=files,
        data=data,
    )
    return resp.json()

def upload_file(
    host: str,
    filename: str,
    file_content: bytes,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    """
    POST /server/files/upload, multipart form-data - upload KHÔNG in ngay
    (khác `upload_and_print` ở trên - hàm mới riêng, không thêm tham số
    cho `upload_and_print` đã khoá, đúng nguyên tắc "khoá" mục 1
    Loop-Controller). Dùng cho E4-1 (upload file G-code lên máy qua
    dashboard, không yêu cầu in ngay).

    Field `print` gửi TƯỜNG MINH = "false" (vẫn multipart form field,
    cùng vị trí/kiểu với `upload_and_print`) - tài liệu Moonraker chính
    thức (moonraker.readthedocs.io/en/latest/external_api/file_manager/,
    mục "File upload") xác nhận default của field `print` vốn đã là
    "false" nếu bỏ qua, nhưng gửi tường minh rõ ràng hơn, không phụ
    thuộc ngầm vào giá trị mặc định (có thể đổi giữa các phiên bản
    Moonraker) - xem docs/State_E4-1_v2.md "Quyết định phạm vi" điểm 1.
    """
    files = {"file": (filename, file_content)}
    data = {"print": "false"}
    resp = _request(
        "POST",
        host,
        "/server/files/upload",
        port=port,
        api_key=api_key,
        timeout=UPLOAD_TIMEOUT_SECONDS,
        files=files,
        data=data,
    )
    return resp.json()

def get_file_metadata(
    host: str,
    filename: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    """
    GET /server/files/metadata?filename=<filename> - lấy metadata file đã
    upload (kích thước, thời gian in ước tính) - dùng cho E4-1.

    Trả NGUYÊN response JSON (không tự parse thêm field cụ thể ở tầng
    này - tầng service, E4-1/C2, chọn field `size`/`estimated_time` cần
    dùng - xem docs/State_E4-1_v2.md "Quyết định phạm vi" điểm 2).

    Xác nhận nguồn (E4-1/C1, KHÔNG nằm trong "Phụ lục: Checklist kỹ
    thuật Moonraker" tham chiếu từ print-farm-manager - dự án đó không
    dùng endpoint này): tài liệu Moonraker chính thức
    (moonraker.readthedocs.io/en/latest/external_api/file_manager/, mục
    "Get GCode Metadata") - response gồm `size` (bytes), `estimated_time`
    (giây), cùng nhiều field khác (`slicer`, `filament_name`, ...) không
    dùng tới ở phạm vi story này. QUAN TRỌNG: response KHÔNG có envelope
    `{"result": {...}}` như các endpoint JSON-RPC-style khác
    (`/server/info`, `/printer/info`, `/printer/objects/query`) - cùng
    kiểu response phẳng như `upload_and_print`/`upload_file` ở trên
    (cả 3 endpoint đều thuộc nhóm File Management, không bọc "result") -
    do đó hàm này trả thẳng `resp.json()`, KHÔNG đọc `["result"]`.
    """
    resp = _request(
        "GET",
        host,
        "/server/files/metadata",
        port=port,
        api_key=api_key,
        params={"filename": filename},
    )
    return resp.json()

def cancel_job(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    """POST /printer/print/cancel - không cần body."""
    resp = _request("POST", host, "/printer/print/cancel", port=port, api_key=api_key)
    return resp.json()

def pause_job(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    """
    POST /printer/print/pause - không cần body.

    Xác nhận nguồn (chunk C1b, KHÔNG nằm trong "Phụ lục: Checklist kỹ thuật
    Moonraker" tham chiếu từ print-farm-manager - dự án đó không dùng
    pause/resume): tài liệu Moonraker chính thức (moonraker.readthedocs.io,
    trang "API Changes") liệt kê endpoint HTTP `/printer/print/pause`
    tương ứng phương thức JSON-RPC `printer.print.pause`, cùng nhóm với
    `/printer/print/cancel` đã dùng ở `cancel_job`. Chạy gcode "PAUSE"
    (có thể bị override bằng gcode_macro tuỳ cấu hình máy - hành vi phía
    firmware, không ảnh hưởng cách gọi HTTP ở đây).
    """
    resp = _request("POST", host, "/printer/print/pause", port=port, api_key=api_key)
    return resp.json()

def resume_job(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    """
    POST /printer/print/resume - không cần body.

    Cùng nguồn xác nhận như `pause_job` (xem docstring ở trên). Chạy gcode
    "RESUME" (có thể bị override bằng gcode_macro tuỳ cấu hình máy).
    """
    resp = _request("POST", host, "/printer/print/resume", port=port, api_key=api_key)
    return resp.json()

def emergency_stop(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> dict:
    """
    POST /printer/emergency_stop - không cần body.

    Xác nhận nguồn (chunk E3-2/C1, KHÔNG nằm trong "Phụ lục: Checklist kỹ
    thuật Moonraker" tham chiếu từ print-farm-manager - dự án đó không có
    tính năng E-Stop tách biệt khỏi cancel): tài liệu Moonraker chính thức
    (moonraker.readthedocs.io/en/latest/external_api/printer/) mô tả
    endpoint này là "immediately halt the printer and put it in a
    'shutdown' state", khuyến nghị dùng cho nút Emergency Stop thay vì
    gửi gcode `M112` qua hàng đợi ("Clients should not send M112 via
    gcode... use the new API"), vì lệnh này không đi qua hàng đợi gcode -
    đáng tin cậy hơn trong tình huống khẩn cấp. Đưa Klippy vào trạng thái
    `webhooks.state != 'ready'`, khiến `get_status()` (đã có sẵn, KHÔNG
    sửa ở đây) tự động trả `OFFLINE` ở lần gọi kế tiếp - không cần thêm
    giá trị canonical mới hay sửa logic map hiện có (D-013).
    """
    resp = _request(
        "POST", host, "/printer/emergency_stop", port=port, api_key=api_key
    )
    return resp.json()

def set_device_power(
    host: str,
    device: str,
    action: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> dict:
    """
    POST /machine/device_power/device - bật/tắt 1 smart plug/device cấu
    hình sẵn trong `moonraker.conf` của máy đích (Machine/Power API, E3-3).

    Xác nhận nguồn (chunk E3-3/C1, KHÔNG nằm trong "Phụ lục: Checklist kỹ
    thuật Moonraker" tham chiếu từ print-farm-manager - dự án đó không có
    tính năng quản lý nguồn điện qua smart plug): tài liệu Moonraker
    chính thức (moonraker.readthedocs.io/en/latest/external_api/devices/,
    mục "Power Endpoints" / "Set Device State") - JSON body bắt buộc gồm
    đúng 2 field `device` (tên device đã cấu hình trong
    `[power <device_name>]`) và `action`. Chỉ dùng `action` thuộc
    `{"on", "off"}` ở phạm vi story này (Moonraker còn hỗ trợ `"toggle"`,
    KHÔNG dùng - AC gốc E3-3 yêu cầu bật/tắt tường minh, không phải đảo
    trạng thái). Endpoint chỉ tồn tại khi máy đích có ít nhất 1 section
    `[power <tên>]` cấu hình trong `moonraker.conf` (khác các endpoint
    `/printer/...` - luôn có sẵn mặc định).

    Trả về JSON dạng `{"<device>": "on"|"off"}` (trạng thái device SAU
    lệnh) - không tự diễn giải/map sang canonical status ở đây (D-013);
    việc đọc lại `printers.status` sau lệnh Power API là trách nhiệm của
    tầng service (`driver.get_status()`, gọi riêng), không phải hàm này.
    """
    resp = _request(
        "POST",
        host,
        "/machine/device_power/device",
        port=port,
        api_key=api_key,
        json={"device": device, "action": action},
    )
    return resp.json()

def check_if_printing(
    host: str, port: int = DEFAULT_MOONRAKER_PORT, api_key: Optional[str] = None
) -> bool:
    """
    Dùng khi client timeout lúc upload nhưng máy có thể đã nhận lệnh (D-011).
    Coi là "đang in" nếu canonical status thuộc {PRINTING, PAUSED}.
    """
    status = get_status(host, port=port, api_key=api_key)
    return status.canonical_status in ACTIVE_JOB_STATUSES

