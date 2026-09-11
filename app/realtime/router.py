"""
FastAPI `APIRouter` cho lớp API giám sát real-time (E2-2/C2+C3) — mount
`GET /printers/{printer_id}/realtime` (snapshot 1 máy),
`GET /printers/realtime` (snapshot toàn bộ máy), và
`GET /printers/realtime/stream` (SSE, đẩy định kỳ, C3).

Đọc `RealtimeStateStore` qua `request.app.state.realtime_store` (được
gán 1 lần lúc `lifespan` startup, `app/main.py`, E2-1/C3) — router này
KHÔNG tự tạo store, không giữ biến toàn cục nào.

Cả 2 route là **async def** (khác toàn bộ route sync hiện có của
`app/printers/router.py`) vì `get_printer_realtime`/
`list_printers_realtime` (`app/realtime/service.py`, C1) đều là
coroutine (`await store.get()`/`get_all()`, `RealtimeStateStore` dùng
`asyncio.Lock` nội bộ) — FastAPI hỗ trợ trộn route sync/async cùng 1
`APIRouter`/app, không có ràng buộc phải đồng nhất.

`GET /printers/{printer_id}/realtime` map `None` (từ
`get_printer_realtime`, nghĩa là `printer_id` không tồn tại trong bảng
`printers`) sang HTTP 404 — cùng style `HTTPException` mà
`app/printers/router.py` đã dùng cho các trường hợp "không tìm thấy
máy in". Khác với trường hợp máy TỒN TẠI nhưng chưa có dữ liệu
realtime (`realtime_connected=False`, Quyết định phạm vi #4,
`docs/State_E2-2_v2.md`) — trường hợp đó vẫn trả 200 bình thường, không
phải lỗi.

Chunk E2-3/C3: thêm route thứ 4 `GET /printers/events` (đọc lại log
cảnh báo, `app/realtime/alerts.py`, C1/C2, Quyết định phạm vi #7,
`docs/State_E2-3_v4.md`) — KHÔNG sửa 3 route trên. Query param tuỳ
chọn `printer_id`/`limit`, gọi thẳng `list_printer_events` (đồng bộ,
không `await` — hàm đó dùng `sqlite3` thô, không phải coroutine, khác
2 route snapshot ở trên). `printer_id` không khớp máy nào trả danh
sách RỖNG (200), KHÔNG 404 — khác hẳn route snapshot 1 máy phía trên,
vì đây là log lịch sử chứ không phải "trạng thái hiện tại của 1 máy cụ
thể phải tồn tại".
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.realtime.alerts import PrinterEventResponse, list_printer_events
from app.realtime.schemas import PrinterRealtimeResponse
from app.realtime.service import (
    get_printer_realtime,
    list_printers_realtime,
    realtime_sse_event_generator,
)

router = APIRouter()

@router.get("/printers/{printer_id}/realtime", response_model=PrinterRealtimeResponse)
async def get_printer_realtime_snapshot(
    printer_id: int, request: Request
) -> PrinterRealtimeResponse:
    """Snapshot real-time (nhiệt độ, % tiến độ, thời gian còn lại) của
    1 máy — poll-on-request, dùng cho lần tải trang đầu hoặc client
    không cần stream liên tục (AC gốc E2-2)."""
    result = await get_printer_realtime(printer_id, request.app.state.realtime_store)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy máy in id={printer_id}.",
        )
    return result

@router.get("/printers/realtime", response_model=List[PrinterRealtimeResponse])
async def list_printers_realtime_snapshot(
    request: Request,
) -> List[PrinterRealtimeResponse]:
    """Snapshot real-time của TOÀN BỘ máy đã đăng ký — dùng làm state
    khởi tạo cho dashboard trước khi stream SSE (`/printers/realtime/
    stream`, C3) kết nối. Danh sách rỗng nếu chưa có máy nào đăng ký —
    không phải lỗi."""
    return await list_printers_realtime(request.app.state.realtime_store)

@router.get("/printers/realtime/stream")
async def stream_printers_realtime(request: Request) -> StreamingResponse:
    """Server-Sent Events (SSE) — đẩy snapshot real-time của TOÀN BỘ
    máy mỗi `REALTIME_STREAM_INTERVAL_SECONDS` giây (`app/realtime/
    service.py`, Quyết định phạm vi #2/#3, `docs/State_E2-2_v2.md`).

    Route là **async generator function** riêng (khác signature 2 route
    snapshot ở trên) — nhận `Request` để truyền `request.is_disconnected`
    vào `realtime_sse_event_generator` (dừng generator sạch khi client
    rời đi, không rò rỉ task nền chạy vô hạn) và đọc
    `request.app.state.realtime_store`."""
    return StreamingResponse(
        realtime_sse_event_generator(
            request.app.state.realtime_store,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
    )

@router.get("/printers/events", response_model=List[PrinterEventResponse])
def list_printer_events_endpoint(
    printer_id: Optional[int] = None, limit: int = 50
) -> List[PrinterEventResponse]:
    """Đọc lại log cảnh báo (bảng `events`, `app/realtime/alerts.py`) —
    lọc theo `printer_id` nếu truyền, sắp mới nhất trước, giới hạn
    `limit` (chặn trần ở `MAX_LIST_LIMIT=200` bên trong
    `list_printer_events`, kể cả nếu client truyền giá trị lớn hơn).
    `printer_id` không khớp máy nào trong bảng `events` trả về danh
    sách rỗng, KHÔNG phải 404 (Quyết định phạm vi #7, `docs/
    State_E2-3_v4.md`) — khác 2 route snapshot phía trên. Route sync
    (khác 3 route trên là `async def`) vì `list_printer_events` dùng
    `sqlite3` thô đồng bộ, không có `await` nào bên trong."""
    return list_printer_events(printer_id=printer_id, limit=limit)
