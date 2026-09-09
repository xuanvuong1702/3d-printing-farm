
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

def test_driver_instance_reusable_across_calls(simulator: int) -> None:
    driver = _driver(simulator)
    assert driver.get_status().canonical_status == hc.CANONICAL_IDLE
    driver.upload_and_print("a.gcode", b"; fake")
    assert driver.get_status().canonical_status == hc.CANONICAL_PRINTING
    driver.cancel_job()
    assert driver.get_status().canonical_status == hc.CANONICAL_STOPPED
