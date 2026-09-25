# QIDI Print Farm — Odoo Printing Farm

Dịch vụ điều phối (orchestration) cho một farm máy in 3D chạy firmware
Klipper/Moonraker (ví dụ dòng máy QIDI). Backend viết bằng **FastAPI +
Uvicorn**, lưu dữ liệu bằng **SQLite** (không ORM), giao tiếp với từng
máy in qua **Moonraker API** (HTTP + WebSocket), và render dashboard
server-side bằng **Jinja2 + HTMX + Alpine.js**.

> Tên repo có chữ "odoo" nhưng dự án **không** phụ thuộc Odoo — đây là
> service độc lập, tự chạy bằng Uvicorn.

## Kiến trúc tóm tắt

- `app/main.py` — entrypoint FastAPI, mount router + các scheduler nền
  (heartbeat, websocket pool, alert watcher, dispatch, history sync).
- `app/db/migrate.py` — tạo schema SQLite (file mặc định `print_farm.db`
  tại thư mục làm việc).
- `app/printers/`, `app/dispatch/`, `app/heartbeat/`, `app/realtime/`,
  `app/history/`, `app/reports/`, `app/spools/` — các domain nghiệp vụ.
- `app/moonraker/`, `app/drivers/` — tầng giao tiếp với Moonraker.
- `app/templates/`, `app/static/` — dashboard server-side (`GET /dashboard`).
- `deploy/qidi-farm.service` — unit file systemd dùng để chạy production
  trên Raspberry Pi.

## Yêu cầu chung

- Python **3.12** (khớp CI, xem `.github/workflows/ci.yml`).
- Các máy in phải bật Moonraker (Klipper) và truy cập được qua mạng LAN
  từ máy chạy service này.

---

## 1. Triển khai trên Raspberry Pi (production)

Giả định Raspberry Pi OS (Debian-based), user chạy service là `qidifarm`,
thư mục cài đặt là `/opt/qidi-farm` (khớp `deploy/qidi-farm.service`).

### 1.1. Cài Python và công cụ hệ thống

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### 1.2. Tạo user chạy service (nếu chưa có)

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin qidifarm
```

### 1.3. Clone mã nguồn vào `/opt/qidi-farm`

```bash
sudo mkdir -p /opt/qidi-farm
sudo chown qidifarm:qidifarm /opt/qidi-farm
sudo -u qidifarm git clone <URL_REPO> /opt/qidi-farm
cd /opt/qidi-farm
```

### 1.4. Tạo virtualenv và cài dependency

```bash
sudo -u qidifarm python3 -m venv /opt/qidi-farm/venv
sudo -u qidifarm /opt/qidi-farm/venv/bin/pip install --upgrade pip
sudo -u qidifarm /opt/qidi-farm/venv/bin/pip install -r requirements.txt
```

> Chỉ cần `requirements.txt` cho production — `requirements-dev.txt`
> (pytest, respx, pyflakes) chỉ dùng khi phát triển/kiểm thử.

### 1.5. Khởi tạo database

Database SQLite mặc định là `print_farm.db`, tạo **tương đối theo
`WorkingDirectory`** (không hard-code đường dẫn tuyệt đối trong code),
nên phải chạy migration ngay tại `/opt/qidi-farm`:

```bash
cd /opt/qidi-farm
sudo -u qidifarm /opt/qidi-farm/venv/bin/python -m app.db.migrate
```

Lệnh này tạo file `/opt/qidi-farm/print_farm.db` với đầy đủ bảng
(`printers`, `jobs`, `job_history`, `events`, `users`).

### 1.6. Cài đặt systemd service

File `deploy/qidi-farm.service` đã sẵn có trong repo, chỉ cần copy vào
đúng vị trí:

```bash
sudo cp /opt/qidi-farm/deploy/qidi-farm.service /etc/systemd/system/qidi-farm.service
sudo systemctl daemon-reload
sudo systemctl enable --now qidi-farm
```

Service sẽ chạy:

```
/opt/qidi-farm/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

với `WorkingDirectory=/opt/qidi-farm` (đúng nơi chứa `print_farm.db`
đã migrate ở bước trên) và tự khởi động lại nếu crash (`Restart=always`).

### 1.7. Kiểm tra

```bash
sudo systemctl status qidi-farm
curl http://localhost:8000/
```

Truy cập dashboard từ máy khác trong LAN: `http://<IP-Raspberry-Pi>:8000/dashboard`.

### 1.8. Xem log

```bash
journalctl -u qidi-farm -f
```

### 1.9. Cập nhật lên bản mới

```bash
cd /opt/qidi-farm
sudo -u qidifarm git pull
sudo -u qidifarm /opt/qidi-farm/venv/bin/pip install -r requirements.txt
sudo systemctl restart qidi-farm
```

> Nếu có thay đổi schema DB, chạy lại bước 1.5 (`python -m app.db.migrate`)
> trước khi restart — migration hiện tại chỉ `CREATE TABLE`, an toàn khi
> chạy lại nhiều lần trên DB đã tồn tại (không có lệnh `DROP`).

### 1.10. Đăng ký máy in vào farm

Sau khi service chạy, đăng ký từng máy in qua API (mỗi máy phải có
Moonraker đang chạy và trỏ đúng IP):

```bash
curl -X POST http://localhost:8000/printers \
  -H "Content-Type: application/json" \
  -d '{"name": "Printer 1", "ip_address": "192.168.1.50"}'
```

Service sẽ tự validate kết nối Moonraker trước khi lưu (lỗi kết nối trả
về HTTP 422, IP trùng trả về 409).

---

## 2. Môi trường dev trên Windows

### 2.1. Cài Python 3.12

Tải và cài từ [python.org](https://www.python.org/downloads/) — nhớ tick
**"Add python.exe to PATH"** khi cài đặt. Kiểm tra:

```powershell
python --version
```

### 2.2. Clone repo

```powershell
git clone <URL_REPO> odoo-printing-farm
cd odoo-printing-farm
```

### 2.3. Tạo virtualenv

```powershell
python -m venv venv
venv\Scripts\activate
```

> PowerShell có thể chặn script kích hoạt venv (lỗi "running scripts is
> disabled"). Nếu gặp, chạy một lần (với quyền user hiện tại):
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2.4. Cài dependency (bao gồm dev)

```powershell
pip install --upgrade pip
pip install -r requirements-dev.txt
```

(`requirements-dev.txt` tự kéo theo `requirements.txt` + `pytest`,
`respx`, `pyflakes`.)

### 2.5. Khởi tạo database local

```powershell
python -m app.db.migrate
```

Tạo file `print_farm.db` ngay tại thư mục repo (thư mục hiện hành).

### 2.6. Chạy server dev (auto-reload)

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Mở trình duyệt: `http://localhost:8000/dashboard`

Tài liệu API tự động (Swagger UI) của FastAPI: `http://localhost:8000/docs`

### 2.7. Chạy test + lint (giống CI)

```powershell
pytest tests/
pyflakes app/ tools/
```

CI (`.github/workflows/ci.yml`) chạy đúng 2 lệnh trên cộng thêm bước
kiểm tra không còn `TODO/FIXME/XXX/NotImplemented` trong `app/` và
`tools/` — nên chạy `pytest` + `pyflakes` local trước khi push để đỡ
fail CI.

### 2.8. Giả lập máy in để test không cần phần cứng thật

Repo có sẵn `tools/moonraker_simulator/` — một Moonraker giả lập, hữu
ích để phát triển/test trên Windows mà không cần Raspberry Pi hay máy
in thật kết nối trực tiếp.

---

## 3. Hướng dẫn sử dụng hệ thống

Phần này mô tả cách **vận hành hàng ngày** sau khi service đã chạy
(xem mục 1/2 ở trên để cài đặt). Toàn bộ ví dụ dùng `localhost:8000`
— khi dùng thật, thay bằng IP Raspberry Pi trong LAN.

### 3.1. Dashboard (giao diện web)

- **`GET /dashboard`** — trang chính, hiển thị grid card tất cả máy in
  đã đăng ký: trạng thái realtime (IDLE/PRINTING/PAUSED/ERROR/OFFLINE),
  % tiến độ, thời gian còn lại. Cập nhật tự động qua Server-Sent Events
  (`/printers/realtime/stream`), không cần refresh trang.
- **`GET /printers/{printer_id}/detail`** — trang chi tiết 1 máy: tab
  Overview (trạng thái + tiến độ realtime), tab Log (lịch sử job đã in
  trên máy đó), tab Camera (webcam stream nếu Moonraker có cấu hình).

### 3.2. Đăng ký và quản lý máy in

```bash
# Thêm máy in mới (validate kết nối Moonraker ngay khi đăng ký)
curl -X POST http://localhost:8000/printers \
  -H "Content-Type: application/json" \
  -d '{"name": "Printer 1", "ip_address": "192.168.1.50"}'

# Xem danh sách máy + trạng thái hiện tại
curl http://localhost:8000/printers

# Sửa tên/model/api_key (KHÔNG sửa được IP/port qua route này)
curl -X PATCH http://localhost:8000/printers/1 \
  -H "Content-Type: application/json" -d '{"name": "Printer 1 - Xưởng A"}'

# Xoá máy (chặn nếu máy đang PRINTING/PAUSED hoặc còn job liên quan)
curl -X DELETE http://localhost:8000/printers/1
```

### 3.3. Gửi lệnh in

```bash
# Upload G-code và in ngay trên 1 máy cụ thể
curl -X POST http://localhost:8000/printers/1/print/start \
  -F "file=@model.gcode"

# Upload file lên máy nhưng chưa in ngay (vào hàng đợi/lưu trên máy)
curl -X POST http://localhost:8000/printers/1/files -F "file=@model.gcode"

# Tự động chọn máy "rảnh nhất" trong farm rồi in luôn — không cần biết
# trước printer_id (hữu ích khi vận hành viên chỉ muốn "in cho xong")
curl -X POST http://localhost:8000/printers/auto-assign/files \
  -F "file=@model.gcode"

# Điều khiển job đang chạy trên máy 1
curl -X POST http://localhost:8000/printers/1/print/pause
curl -X POST http://localhost:8000/printers/1/print/resume
curl -X POST http://localhost:8000/printers/1/print/cancel

# Sắp xếp lại thứ tự hàng đợi in của máy 1 (job_ids là hoán vị đầy đủ
# các job đang ở trạng thái "queued" của máy đó)
curl -X POST http://localhost:8000/printers/1/queue/reorder \
  -H "Content-Type: application/json" -d '{"job_ids": [5, 3, 4]}'
```

### 3.4. An toàn, nguồn điện và xác nhận vận hành viên

```bash
# Emergency Stop — bắt buộc phải confirm=true, nếu không sẽ bị chặn (422)
curl -X POST http://localhost:8000/printers/1/emergency_stop \
  -H "Content-Type: application/json" -d '{"confirm": true}'

# Bật/tắt nguồn máy in từ xa (chỉ hoạt động nếu máy có smart plug được
# cấu hình qua Moonraker Power API — nếu không sẽ trả 409)
curl -X POST http://localhost:8000/printers/1/power/on
curl -X POST http://localhost:8000/printers/1/power/off

# Sau khi job kết thúc (FINISHED/ERROR), máy bị "khoá" (is_held) chờ
# vận hành viên kiểm tra thực tế rồi mới xác nhận gỡ khoá:
curl -X POST http://localhost:8000/printers/1/confirm
```

### 3.5. Theo dõi realtime, báo cáo và vật liệu (spool)

```bash
# Trạng thái realtime của 1 máy / toàn bộ farm (poll một lần)
curl http://localhost:8000/printers/1/realtime
curl http://localhost:8000/printers/realtime

# Stream realtime liên tục (Server-Sent Events) — dashboard web dùng
# chính endpoint này để tự cập nhật
curl -N http://localhost:8000/printers/realtime/stream

# Lịch sử sự kiện/cảnh báo (lỗi, mất kết nối...) của farm
curl http://localhost:8000/printers/events

# Báo cáo tổng hợp: tổng giờ chạy, tỷ lệ lỗi, sản lượng — lọc theo máy
# và/hoặc khoảng thời gian (ISO-8601, since/before đều optional)
curl "http://localhost:8000/reports/summary?printer_id=1&since=2026-09-01T00:00:00Z"

# Thông tin vật liệu (Spoolman) đã dùng cho job trên 1 máy cụ thể
curl "http://localhost:8000/spools/3?printer_id=1"
```

### 3.6. Tài liệu API tự động

FastAPI tự sinh Swagger UI tương tác tại `http://localhost:8000/docs`
(và OpenAPI JSON tại `/openapi.json`) — liệt kê đầy đủ mọi route, kiểu
dữ liệu request/response, và cho phép gọi thử trực tiếp trên trình
duyệt mà không cần `curl`.

---

## 4. Ghi chú chung

- Database là SQLite thuần (không ORM), nên **không cần** cài đặt
  server DB riêng (không Postgres/MySQL).
- Không có biến môi trường (`.env`) bắt buộc nào ở thời điểm hiện tại —
  cấu hình máy in nằm trong DB, thêm qua API `/printers`.
- Xem `CLAUDE.md` trong repo để biết quy ước code/kiến trúc chi tiết
  hơn nếu cần đóng góp code.
