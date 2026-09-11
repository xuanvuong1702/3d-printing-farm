"""
Kênh WebSocket real-time tới Moonraker cho từng máy in (E2-1).

Package MỚI, tách biệt hoàn toàn khỏi `app/drivers/`/
`app/moonraker/http_client.py`/`app/heartbeat/` (Quyết định 5,
`docs/State_E2-1_v2.md` mục "Quyết định phạm vi chốt tại chunk C0") - lý
do: `PrinterDriver` (D-006/D-012, `app/drivers/base.py`) là interface
ĐỒNG BỘ (mỗi method gọi thẳng 1 hàm module-level đồng bộ trong
`http_client.py`, D-002 phần 1), trong khi kênh WS ở đây là bất đồng bộ
hoàn toàn (native `asyncio`/`aiohttp` qua thư viện `moonraker-api`,
D-002 phần 2 / Q-001) - thêm method bất đồng bộ vào 1 interface đồng bộ
sẽ phá vỡ tính nhất quán của tầng nghiệp vụ hiện tại (luôn gọi driver
kiểu blocking call). Vì vậy `app/realtime/` tự đọc `host`/`port`/
`api_key` trực tiếp từ bảng `printers` (cùng cách
`app/heartbeat/service.py` đã làm), không đi qua `resolve_driver`.

Phạm vi qua các chunk (xem `docs/State_E2-1_v2.md` mục "Chunk plan" cho
chi tiết đầy đủ):
- C1 (chunk này): CHỈ data layer - `RealtimePrinterState` (dataclass) +
  store in-memory (`printer_id -> RealtimePrinterState`) + hằng số cấu
  hình (object list subscribe, backoff reconnect). CHƯA kết nối WS
  thật.
- C2: wrapper 1 kết nối (`MoonrakerClient`/`MoonrakerListener` của thư
  viện `moonraker-api`) cho 1 máy - connect + subscribe + map
  notification vào store.
- C3: pool N kết nối đồng thời (1 `asyncio.Task`/máy) + reconnect với
  backoff cấp số nhân (Quyết định 2 - thư viện KHÔNG tự reconnect) +
  wiring vào `app/main.py` `lifespan`.
- C4: xử lý lỗi/edge case (máy lỗi không ảnh hưởng máy khác, không rò
  rỉ task/connection khi reconnect liên tục).
- C5: test - mở rộng `tools/moonraker_simulator/` thêm route WebSocket
  + `tests/realtime/`.

Quyết định 4 (lưu trữ in-memory, KHÔNG ghi DB): dữ liệu real-time
(nhiệt độ/tiến độ/trạng thái) đổi liên tục (nhiều lần/giây khi máy đang
in) - ghi SQLite mỗi notification là quá tải không cần thiết cho Pi 4
và không mang lại giá trị bền vững (service restart -> pool tự kết nối
+ subscribe lại ngay, dữ liệu mới có ngay lập tức, không cần khôi phục
từ đâu cả). Khác Quyết định 4 của E1-4 (backoff heartbeat PHẢI bền
trong SQLite vì khoảng cách 2 lần thử có thể lên tới 300s, service
restart giữa chừng sẽ mất tiến trình backoff).
"""
