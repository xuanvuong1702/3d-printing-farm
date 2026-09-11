"""
Interface driver chung (`PrinterDriver`) + driver mặc định theo chuẩn
Moonraker/Klipper (`BaseKlipperDriver`).

Phạm vi chunk E0-6/C1 — chỉ interface + driver mặc định. KHÔNG bao gồm
cơ chế resolution theo `(model, firmware_version_range)` → `model` →
mặc định (đó là chunk C2, `app/drivers/registry.py`).

Quyết định áp dụng (xem `docs/Decisions.md` để biết nội dung đầy đủ,
không copy lại ở đây):
- D-006: tách driver theo model/firmware thay vì 1 client dùng chung —
  `PrinterDriver` là interface mà D-006 AC yêu cầu ("tầng nghiệp vụ chỉ
  gọi qua interface driver chung").
- D-012 mục 1: có đúng 1 driver mặc định (`BaseKlipperDriver`), triển
  khai đầy đủ interface theo chuẩn Moonraker/Klipper, luôn tồn tại,
  dùng làm điểm rơi cuối cùng khi chưa có driver riêng cho model/version
  cụ thể. Driver riêng (nếu phát sinh ở story sau, khi "Ma trận tương
  thích" ghi nhận sai khác thật) sẽ kế thừa `BaseKlipperDriver` và
  override đúng (các) phương thức lệch (D-012 mục 2), KHÔNG viết lại
  toàn bộ trừ khi giao thức nền tảng đổi hẳn.
- D-013: `BaseKlipperDriver.get_status()` trả về đúng tập canonical
  status đã chốt — không tự map thêm lần nữa, vì
  `app/moonraker/http_client.py::get_status` (D-002 phần 1) đã map sẵn.
- D-002 phần (1): `BaseKlipperDriver` KHÔNG tự gọi `httpx` hay thư viện
  trung gian nào — mỗi method chỉ gọi thẳng đúng 1 hàm module-level
  tương ứng, đã có sẵn và đã khoá, trong `app/moonraker/http_client.py`.
  Không viết lại logic map/tính toán bên trong các hàm đó.
- CLAUDE.md nguyên tắc #2: driver không bao giờ chạm DB — constructor và
  mọi method ở đây chỉ nhận/dùng `host`/`port`/`api_key` (kết nối tới
  Moonraker), không mở kết nối DB, không import `app.db`. Việc ghi DB là
  trách nhiệm của tầng service (story sau) khi gọi qua driver.

Tập phương thức của interface bám sát đúng 9 hàm module-level hiện có
trong `http_client.py` ở E0-6 (không thêm phương thức nào ngoài nhu cầu
thật đã có bằng chứng — AC gốc E0-6 không yêu cầu tính năng nào khác):
`get_server_info`, `get_printer_info`, `get_status`, `gcode_script`,
`upload_and_print`, `cancel_job`, `pause_job`, `resume_job`,
`check_if_printing`. Bổ sung `emergency_stop` (E3-2) và `set_power`
(E3-3) ở các story sau, cùng nguyên tắc "chỉ thêm khi có bằng chứng nhu
cầu thật từ AC".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.moonraker import http_client
from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT, PrinterStatus

class PrinterDriver(ABC):
    """
    Interface driver chung — tầng nghiệp vụ (monitoring, control, queue)
    chỉ được gọi qua các phương thức khai báo ở đây (D-006 AC 1), không
    bao giờ gọi thẳng `app.moonraker.http_client` hay biết về việc bên
    dưới đang dùng Moonraker/Klipper cụ thể ra sao.

    Một instance của driver gắn với đúng 1 máy in cụ thể (`host`/`port`/
    `api_key` cố định tại thời điểm khởi tạo) — không truyền lại các
    tham số này ở mỗi lần gọi method, khác với các hàm module-level của
    `http_client.py` (vốn nhận `host` ở mọi lệnh gọi vì không có khái
    niệm driver instance).
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_MOONRAKER_PORT,
        api_key: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key

    @abstractmethod
    def get_server_info(self) -> dict:
        """Thông tin server Moonraker (dùng ở E0-2 để xác nhận cổng/API, health-check)."""

    @abstractmethod
    def get_printer_info(self) -> dict:
        """Phiên bản Moonraker/Klipper — dùng cho capability detection (D-007, E1-1)."""

    @abstractmethod
    def get_status(self) -> PrinterStatus:
        """Trạng thái máy đã map sang canonical (D-013) + progress/timeRemaining/filename."""

    @abstractmethod
    def gcode_script(self, script: str) -> dict:
        """Chạy một dòng/script gcode tuỳ ý."""

    @abstractmethod
    def upload_and_print(self, filename: str, file_content: bytes) -> dict:
        """Upload G-code + in ngay."""

    @abstractmethod
    def cancel_job(self) -> dict:
        """Huỷ job đang in."""

    @abstractmethod
    def pause_job(self) -> dict:
        """Tạm dừng job đang in."""

    @abstractmethod
    def resume_job(self) -> dict:
        """Tiếp tục job đang bị tạm dừng."""

    @abstractmethod
    def check_if_printing(self) -> bool:
        """True nếu canonical status thuộc {PRINTING, PAUSED} (dùng ở D-011)."""

    @abstractmethod
    def emergency_stop(self) -> dict:
        """Dừng khẩn cấp (E-Stop) - đưa Klippy vào trạng thái 'shutdown' (E3-2)."""

    @abstractmethod
    def set_power(self, device_name: str, action: str) -> dict:
        """Bật/tắt 1 smart plug/device qua Machine/Power API (E3-3).

        `device_name` truyền vào MỖI LẦN GỌI (khác `host`/`port`/
        `api_key` cố định ở constructor) - đây là dữ liệu cấu hình theo
        từng máy (cột `printers.power_device_name`, đọc ở tầng service),
        không phải thuộc tính cố định của kết nối Moonraker. `action`
        thuộc `{"on", "off"}` (xem `docs/State_E3-3_v2.md` mục "Quyết
        định phạm vi" điểm 2 - không dùng `"toggle"`)."""

class BaseKlipperDriver(PrinterDriver):
    """
    Driver mặc định theo chuẩn Moonraker/Klipper (D-012 mục 1) — luôn
    tồn tại, dùng làm điểm rơi cuối cùng cho bất kỳ máy nào chưa có
    driver riêng. Mỗi method chỉ bọc quanh đúng 1 hàm module-level tương
    ứng trong `app/moonraker/http_client.py` (đã khoá, KHÔNG sửa logic
    bên trong các hàm đó ở đây).

    Driver riêng cho model/firmware lệch chuẩn (khi phát sinh, story
    sau) kế thừa class này và override đúng (các) phương thức bị lệch
    (D-012 mục 2) — ví dụ chỉ override `get_status()` nếu một model cụ
    thể trả sai lệch field trong `print_stats`, các method khác giữ
    nguyên hành vi từ class này.
    """

    def get_server_info(self) -> dict:
        return http_client.get_server_info(
            self.host, port=self.port, api_key=self.api_key
        )

    def get_printer_info(self) -> dict:
        return http_client.get_printer_info(
            self.host, port=self.port, api_key=self.api_key
        )

    def get_status(self) -> PrinterStatus:
        return http_client.get_status(self.host, port=self.port, api_key=self.api_key)

    def gcode_script(self, script: str) -> dict:
        return http_client.gcode_script(
            self.host, script, port=self.port, api_key=self.api_key
        )

    def upload_and_print(self, filename: str, file_content: bytes) -> dict:
        return http_client.upload_and_print(
            self.host,
            filename,
            file_content,
            port=self.port,
            api_key=self.api_key,
        )

    def cancel_job(self) -> dict:
        return http_client.cancel_job(self.host, port=self.port, api_key=self.api_key)

    def pause_job(self) -> dict:
        return http_client.pause_job(self.host, port=self.port, api_key=self.api_key)

    def resume_job(self) -> dict:
        return http_client.resume_job(self.host, port=self.port, api_key=self.api_key)

    def check_if_printing(self) -> bool:
        return http_client.check_if_printing(
            self.host, port=self.port, api_key=self.api_key
        )

    def emergency_stop(self) -> dict:
        return http_client.emergency_stop(
            self.host, port=self.port, api_key=self.api_key
        )

    def set_power(self, device_name: str, action: str) -> dict:
        return http_client.set_device_power(
            self.host, device_name, action, port=self.port, api_key=self.api_key
        )
