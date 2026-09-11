"""
FastAPI `APIRouter` cho domain "Printer Registry" (E1-1/C1, E1-2/C1,
E1-3/C1) — mount `POST /printers`, `GET /printers`,
`PATCH /printers/{printer_id}`, `DELETE /printers/{printer_id}`.

Router chỉ chịu trách nhiệm map exception/giá trị trả về nghiệp vụ
(`service.py`) sang HTTP status code đúng AC gốc E1-1 ("validate kết
nối được tới Moonraker trước khi lưu" → lỗi kết nối trả HTTP rõ ràng,
không phải 500 chung chung; IP trùng → 409 Conflict, không phải 500) —
không chứa logic nghiệp vụ nào khác.

`GET /printers` (E1-2) không map exception nào riêng — `list_printers`
đã tự bắt `MoonrakerClientError` cho từng máy và map sang `OFFLINE`
(xem `app/printers/service.py`), nên không có lỗi nghiệp vụ nào lộ ra
tới tầng router ở đây.

`PATCH`/`DELETE /printers/{printer_id}` (E1-3) map `None`/chuỗi kết
quả của `update_printer`/`delete_printer` sang `404`/`409` — không
dùng exception cho nhánh nghiệp vụ "có job active" (xem
`docs/State_E1-3_v2.md`).

`POST /printers/{printer_id}/print/{start,pause,resume,cancel}`
(E3-1/C2) map `None` → `404` (cùng tiền lệ PATCH/DELETE E1-3),
`PrinterCommandError` (lỗi gọi Moonraker khi điều khiển job) → `502
Bad Gateway` (mã mới, khác `422` của `PrinterConnectionError` ở
`create_printer` — đây là lỗi xảy ra SAU khi máy đã đăng ký/đang vận
hành, không phải lỗi validate lúc đăng ký). Xem
`docs/State_E3-1_v3.md` mục "Quyết định phạm vi" #2-#5 cho rationale
đầy đủ (không lặp lại ở đây): "start" chỉ hỗ trợ upload file mới +
in ngay (multipart, field `file`), KHÔNG hỗ trợ start file đã có sẵn
trên máy qua tên file (thuộc phạm vi E4-1).

`POST /printers/{printer_id}/emergency_stop` (E3-2/C3) — đặt NGOÀI
namespace `/print/` (khác 4 route trên), body `EmergencyStopRequest`
(`confirm: bool = False`). `confirm` không phải `true` → `422` (kiểm
tra TRƯỚC khi gọi service); `None`/`PrinterCommandError` map `404`/
`502` cùng tiền lệ 4 route `/print/`. Xem `docs/State_E3-2_v4.md` mục
"Quyết định phạm vi" #4-#7 cho rationale đầy đủ.

`POST /printers/{printer_id}/power/{on,off}` (E3-3/C3) — đặt NGOÀI
namespace `/print/` (cùng tiền lệ `emergency_stop`), KHÔNG có body
request (khác `emergency_stop`, AC gốc E3-3 không yêu cầu xác nhận
riêng). `None` → `404`; `PrinterPowerNotSupportedError`/
`PrinterPowerNotConfiguredError` (máy không hỗ trợ/chưa cấu hình
`power_device_name`) → `409 Conflict` (2 thông điệp khác nhau, cùng
mã); `PrinterCommandError` → `502` cùng tiền lệ. Xem
`docs/State_E3-3_v2.md` mục "Quyết định phạm vi" #6-#9 cho rationale
đầy đủ.

`POST /printers/{printer_id}/confirm` (E3-4/C3) — "xác nhận vận hành
viên", gỡ `is_held` sau khi operator đã xử lý xong máy bị khoá do job
`FINISHED`/`ERROR` (AC gốc E3-4, D-010 điểm 6). Đặt NGOÀI namespace
`/print/` (cùng tiền lệ `emergency_stop`/`power`), KHÔNG có body
request (cùng tiền lệ `power`, E3-3 — AC gốc không yêu cầu xác nhận
gì thêm trong body). `None` → `404`; `PrinterNotHeldError` (máy hiện
`is_held = 0`, không có gì để xác nhận) → `409 Conflict`. Xem
`docs/State_E3-4_v2.md` mục "Quyết định phạm vi" #6 cho rationale đầy
đủ.

`POST /printers/{printer_id}/files` (E4-1/C3) — upload file G-code
(đã cắt lớp sẵn) lên máy `printer_id`, KHÔNG in ngay (khác
`/print/start`, E3-1 — endpoint đó upload + in ngay; endpoint này chỉ
lưu file + tạo job `queued`, xem `app/printers/service.py::
upload_file_to_printer`, C2). Đặt tên số nhiều "files" (không phải
"upload") để khớp danh từ tài nguyên con của `/printers/{printer_id}/
...` và khớp Moonraker (`/server/files/upload`) — xem
`docs/State_E4-1_v4.md` mục "Quyết định phạm vi" điểm 6. Multipart
form-data, field `file` (cùng kiểu tham số `UploadFile`/`File(...)`
đã dùng ở `/print/start`). `UnsupportedFileTypeError` (không phải
`.gcode`) → `415 Unsupported Media Type`; `None` → `404` (cùng tiền
lệ); `PrinterCommandError` (lỗi Moonraker khi upload/lấy metadata) →
`502 Bad Gateway` (cùng tiền lệ 4 route `/print/`). Thành công trả
`JobResponse` mới, status code `201 Created` (cùng tiền lệ
`POST /printers`, E1-1 — hành động tạo mới, khác `200` của các route
điều khiển/PATCH). Xem `docs/State_E4-1_v4.md` mục "Quyết định phạm
vi" điểm 6-7 cho rationale đầy đủ.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.printers.schemas import (
    EmergencyStopRequest,
    JobResponse,
    PrinterCreateRequest,
    PrinterResponse,
    PrinterUpdateRequest,
)
from app.printers.service import (
    PrinterAlreadyExistsError,
    PrinterCommandError,
    PrinterConnectionError,
    PrinterNotHeldError,
    PrinterPowerNotConfiguredError,
    PrinterPowerNotSupportedError,
    UnsupportedFileTypeError,
    cancel_print,
    confirm_printer,
    delete_printer,
    emergency_stop_printer,
    list_printers,
    pause_print,
    power_off_printer,
    power_on_printer,
    register_printer,
    resume_print,
    start_print,
    update_printer,
    upload_file_to_printer,
)

router = APIRouter()

@router.post(
    "/printers",
    response_model=PrinterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_printer(request: PrinterCreateRequest) -> PrinterResponse:
    """Đăng ký 1 máy in mới bằng IP + tên (AC gốc E1-1)."""
    try:
        return register_printer(request)
    except PrinterConnectionError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrinterAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/printers", response_model=List[PrinterResponse])
def get_printers() -> List[PrinterResponse]:
    """Danh sách máy đã đăng ký kèm trạng thái poll-on-request qua HTTP
    (AC gốc E1-2 — xem `docs/State_E1-2_v2.md` cho rationale phạm vi)."""
    return list_printers()

@router.patch("/printers/{printer_id}", response_model=PrinterResponse)
def patch_printer(printer_id: int, request: PrinterUpdateRequest) -> PrinterResponse:
    """Sửa `name`/`model`/`api_key` của 1 máy đã đăng ký (AC gốc E1-3 —
    xem `docs/State_E1-3_v2.md` cho rationale phạm vi: KHÔNG cho sửa
    `ip`/`moonraker_port`, body rỗng là no-op hợp lệ)."""
    result = update_printer(printer_id, request)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.delete("/printers/{printer_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_printer(printer_id: int) -> None:
    """Xoá cứng 1 máy in, chặn khi đang có job active (AC gốc E1-3 —
    "có job active" = `status` `PRINTING`/`PAUSED`, D-013/D-010)."""
    result = delete_printer(printer_id)
    if result == "not_found":
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    if result == "has_active_job":
        raise HTTPException(
            status_code=409,
            detail="Máy đang có job active (PRINTING/PAUSED), không thể xoá.",
        )
    if result == "has_related_records":
        raise HTTPException(
            status_code=409,
            detail="Máy còn dữ liệu jobs/job_history liên quan, không thể xoá.",
        )

@router.post("/printers/{printer_id}/print/start", response_model=PrinterResponse)
async def start_printer_job(printer_id: int, file: UploadFile = File(...)) -> PrinterResponse:
    """Upload G-code + in ngay trên máy `printer_id` (AC gốc E3-1, phần
    "start") — KHÔNG hỗ trợ start file đã có sẵn trên máy qua tên file
    (xem docstring module này/`docs/State_E3-1_v3.md`)."""
    file_content = await file.read()
    try:
        result = start_print(printer_id, file.filename, file_content)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/print/pause", response_model=PrinterResponse)
def pause_printer_job(printer_id: int) -> PrinterResponse:
    """Tạm dừng job đang in trên máy `printer_id` (AC gốc E3-1, phần
    "pause")."""
    try:
        result = pause_print(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/print/resume", response_model=PrinterResponse)
def resume_printer_job(printer_id: int) -> PrinterResponse:
    """Tiếp tục job đang tạm dừng trên máy `printer_id` (AC gốc E3-1,
    phần "resume")."""
    try:
        result = resume_print(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/print/cancel", response_model=PrinterResponse)
def cancel_printer_job(printer_id: int) -> PrinterResponse:
    """Huỷ job đang in trên máy `printer_id` (AC gốc E3-1, phần
    "cancel")."""
    try:
        result = cancel_print(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post(
    "/printers/{printer_id}/emergency_stop", response_model=PrinterResponse
)
def emergency_stop_printer_endpoint(
    printer_id: int, request: EmergencyStopRequest
) -> PrinterResponse:
    """Gửi lệnh Emergency Stop (E-Stop) tới máy `printer_id` (AC gốc
    E3-2 — "Nút E-Stop riêng biệt, có xác nhận trước khi gửi"). Đặt
    NGOÀI namespace `/print/` (khác 4 route trên) vì đây là lệnh an
    toàn tác động lên toàn máy, không phải thao tác trên "job đang
    chạy" (xem `docs/State_E3-2_v4.md` mục "Quyết định phạm vi" #4).

    `confirm` không phải `true` → `422` (kiểm tra TRƯỚC khi gọi
    service, không chạm DB/Moonraker nếu chưa xác nhận — fail-safe,
    "Quyết định phạm vi" #5). `PrinterCommandError` → `502` (cùng tiền
    lệ 4 route `/print/`); kết quả `None` → `404` (cùng tiền lệ
    PATCH/DELETE E1-3)."""
    if not request.confirm:
        raise HTTPException(
            status_code=422,
            detail="Cần xác nhận (confirm=true) trước khi gửi lệnh Emergency Stop.",
        )
    try:
        result = emergency_stop_printer(printer_id)
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/confirm", response_model=PrinterResponse)
def confirm_printer_endpoint(printer_id: int) -> PrinterResponse:
    """"Xác nhận vận hành viên" — gỡ `is_held` sau khi operator đã xử lý
    xong máy bị khoá do job `FINISHED`/`ERROR` (AC gốc E3-4, D-010 điểm
    6). Không có body request (cùng tiền lệ `power`, xem docstring
    module này)."""
    try:
        result = confirm_printer(printer_id)
    except PrinterNotHeldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/power/on", response_model=PrinterResponse)
def power_on_printer_endpoint(printer_id: int) -> PrinterResponse:
    """Bật nguồn máy in `printer_id` qua Machine/Power API (AC gốc E3-3
    — "bật/tắt nguồn máy in từ xa nếu có smart plug"). Không có body
    request (khác `emergency_stop`, xem docstring module)."""
    try:
        result = power_on_printer(printer_id)
    except (PrinterPowerNotSupportedError, PrinterPowerNotConfiguredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post("/printers/{printer_id}/power/off", response_model=PrinterResponse)
def power_off_printer_endpoint(printer_id: int) -> PrinterResponse:
    """Tắt nguồn máy in `printer_id` qua Machine/Power API (AC gốc E3-3).
    Lỗi đọc lại status SAU KHI lệnh tắt nguồn đã thành công (dự kiến
    mất kết nối vì cắt điện board) KHÔNG bị coi là lỗi — service tự map
    `OFFLINE`, xem `app/printers/service.py::_run_power_command`."""
    try:
        result = power_off_printer(printer_id)
    except (PrinterPowerNotSupportedError, PrinterPowerNotConfiguredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return result

@router.post(
    "/printers/{printer_id}/files",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_printer_file(
    printer_id: int, file: UploadFile = File(...)
) -> JobResponse:
    """Upload file G-code (đã cắt lớp sẵn) lên máy `printer_id`, KHÔNG
    in ngay (AC gốc E4-1 — xem docstring module này/`docs/State_E4-1_v4.md`
    mục "Quyết định phạm vi" điểm 6-7 cho rationale đầy đủ)."""
    file_content = await file.read()
    try:
        result = upload_file_to_printer(printer_id, file.filename, file_content)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except PrinterCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Không tìm thấy máy in id={printer_id}."
        )
    return JobResponse(**result)
