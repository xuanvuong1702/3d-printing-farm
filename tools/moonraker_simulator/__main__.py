"""
Entrypoint chạy Moonraker simulator như 1 tiến trình HTTP riêng.

Quyết định kiến trúc (chốt tại E0-5/C1, xem docs/Story_E0-5.md): chạy
qua Uvicorn, mặc định lắng nghe cổng Moonraker thật
(`DEFAULT_MOONRAKER_PORT` = 7125, D-001, import trực tiếp từ
`app/moonraker/http_client.py` để không lặp lại số cổng ở 2 nơi) trên
mọi interface — để `http_client.py` trỏ `host`/`port` vào thẳng
simulator mà không cần sửa gì ở đó.

Cách chạy thủ công (dev):
    python -m tools.moonraker_simulator
    python -m tools.moonraker_simulator --host 127.0.0.1 --port 17125

`--port` khác mặc định dùng khi cần chạy song song nhiều máy giả lập
(mỗi máy 1 tiến trình, 1 cổng riêng), hoặc cho test tích hợp (chunk C5)
cần cổng ngẫu nhiên/độc lập khi spin simulator qua subprocess.
"""

from __future__ import annotations

import argparse

import uvicorn

from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT
from tools.moonraker_simulator.app import app

def main() -> None:
    """Parse tham số dòng lệnh rồi chạy simulator qua `uvicorn.run`."""
    parser = argparse.ArgumentParser(description="QIDI Moonraker Simulator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_MOONRAKER_PORT)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)

if __name__ == "__main__":
    main()
