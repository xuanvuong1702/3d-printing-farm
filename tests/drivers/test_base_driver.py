
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
    driver = _driver(simulator)
    content = b"; fake gcode content"

    upload_result = driver.upload_file("plate.gcode", content)
    assert upload_result["result"]["print_started"] is False
    assert driver.get_status().canonical_status == hc.CANONICAL_IDLE

    metadata = driver.get_file_metadata("plate.gcode")
    assert metadata == hc.get_file_metadata(SIMULATOR_HOST, "plate.gcode", port=simulator)
    assert metadata["size"] == len(content)

def test_driver_instance_reusable_across_calls(simulator: int) -> None:
    driver = _driver(simulator)
    assert driver.get_status().canonical_status == hc.CANONICAL_IDLE
    driver.upload_and_print("a.gcode", b"; fake")
    assert driver.get_status().canonical_status == hc.CANONICAL_PRINTING
    driver.cancel_job()
    assert driver.get_status().canonical_status == hc.CANONICAL_STOPPED

def test_job_queue_status_enqueue_and_start_uploaded_print(
    simulator: int,
) -> None:
    driver = _driver(simulator)

    empty_status = driver.get_job_queue_status()
    assert empty_status == hc.get_job_queue_status(SIMULATOR_HOST, port=simulator)
    assert empty_status == {"queued_jobs": [], "queue_state": "ready"}

    driver.upload_file("plate.gcode", b"; fake gcode")
    enqueue_result = driver.enqueue_job(["plate.gcode"])
    assert enqueue_result == {
        "queued_jobs": [{"filename": "plate.gcode"}],
        "queue_state": "ready",
    }

    status_after = driver.get_job_queue_status()
    assert status_after == enqueue_result

    assert driver.get_status().canonical_status == hc.CANONICAL_IDLE

def test_start_uploaded_print(simulator: int) -> None:
    driver = _driver(simulator)
    driver.upload_file("cube.gcode", b"; fake gcode")

    result = driver.start_uploaded_print("cube.gcode")

    assert result == "ok"
    assert isinstance(result, str)
    status = driver.get_status()
    assert status.canonical_status == hc.CANONICAL_PRINTING
    assert status.filename == "cube.gcode"
