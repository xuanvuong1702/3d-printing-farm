"""
Test `BaseKlipperDriver` (chunk E0-6/C1), gọi thật qua Moonraker
simulator (`tools/moonraker_simulator`, dùng fixture `simulator` ở
`tests/drivers/conftest.py`).

Mục tiêu của file này KHÁC với
`tests/moonraker_simulator/test_integration.py` (đã test kỹ logic map
canonical/progress/timeRemaining ở tầng `http_client.py` rồi, không lặp
lại ở đây): mục tiêu ở đây là xác nhận **mỗi method của
`BaseKlipperDriver` gọi đúng vào đúng hàm `http_client.py` tương ứng**
(driver bọc đúng, không lệch tham số, không đổi hành vi) — dùng
`BaseKlipperDriver` như tầng nghiệp vụ (story sau) sẽ dùng, thay vì gọi
thẳng `http_client` module-level.
"""

from __future__ import annotations

from app.drivers.base import BaseKlipperDriver
from app.moonraker import http_client as hc
from tests.drivers.conftest import SIMULATOR_HOST

def _driver(port: int, api_key: str | None = None) -> BaseKlipperDriver:
    return BaseKlipperDriver(SIMULATOR_HOST, port=port, api_key=api_key)

def test_get_server_info(simulator: int) -> None:
    driver = _driver(simulator)
    result = driver.get_server_info()
    assert result == hc.get_server_info(SIMULATOR_HOST, port=simulator)

def test_get_printer_info(simulator: int) -> None:
    driver = _driver(simulator)
    result = driver.get_printer_info()
    assert result == hc.get_printer_info(SIMULATOR_HOST, port=simulator)

def test_get_status_idle_by_default(simulator: int) -> None:
    driver = _driver(simulator)
    status = driver.get_status()
    assert status.canonical_status == hc.CANONICAL_IDLE
    assert status.filename is None

def test_upload_and_print_then_status_printing(simulator: int) -> None:
    driver = _driver(simulator)
    upload_result = driver.upload_and_print("benchy.gcode", b"; fake gcode")
    assert upload_result["result"]["print_started"] is True

    status = driver.get_status()
    assert status.canonical_status == hc.CANONICAL_PRINTING
    assert status.filename == "benchy.gcode"

def test_gcode_script(simulator: int) -> None:
    driver = _driver(simulator)
    result = driver.gcode_script("G28")
    assert result["result"] == "ok"

def test_pause_then_resume(simulator: int) -> None:
    driver = _driver(simulator)
    driver.upload_and_print("cube.gcode", b"; fake")

    pause_result = driver.pause_job()
    assert pause_result["result"] == "ok"
    assert driver.get_status().canonical_status == hc.CANONICAL_PAUSED

    resume_result = driver.resume_job()
    assert resume_result["result"] == "ok"
    assert driver.get_status().canonical_status == hc.CANONICAL_PRINTING

def test_cancel_job(simulator: int) -> None:
    driver = _driver(simulator)
    driver.upload_and_print("cube.gcode", b"; fake")

    cancel_result = driver.cancel_job()
    assert cancel_result["result"] == "ok"
    assert driver.get_status().canonical_status == hc.CANONICAL_STOPPED

def test_check_if_printing(simulator: int) -> None:
    driver = _driver(simulator)
    assert driver.check_if_printing() is False

    driver.upload_and_print("cube.gcode", b"; fake")
    assert driver.check_if_printing() is True

def test_upload_file_then_get_file_metadata(simulator: int) -> None:
    """`driver.upload_file` (E4-1) không in ngay, và `driver.get_file_metadata`
    đọc lại đúng size đã upload - đối chiếu với gọi thẳng `http_client`."""
    driver = _driver(simulator)
    content = b"; fake gcode content"

    upload_result = driver.upload_file("plate.gcode", content)
    assert upload_result["result"]["print_started"] is False
    assert driver.get_status().canonical_status == hc.CANONICAL_IDLE

    metadata = driver.get_file_metadata("plate.gcode")
    assert metadata == hc.get_file_metadata(SIMULATOR_HOST, "plate.gcode", port=simulator)
    assert metadata["size"] == len(content)

def test_driver_instance_reusable_across_calls(simulator: int) -> None:
    """1 driver instance (host/port/api_key cố định ở constructor, quyết
    định thiết kế C1) dùng được cho nhiều lệnh gọi liên tiếp trên cùng 1
    máy, không cần truyền lại host/port ở mỗi lệnh."""
    driver = _driver(simulator)
    assert driver.get_status().canonical_status == hc.CANONICAL_IDLE
    driver.upload_and_print("a.gcode", b"; fake")
    assert driver.get_status().canonical_status == hc.CANONICAL_PRINTING
    driver.cancel_job()
    assert driver.get_status().canonical_status == hc.CANONICAL_STOPPED
