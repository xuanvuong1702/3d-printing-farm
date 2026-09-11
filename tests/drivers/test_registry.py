"""
Test `app/drivers/registry.py` (chunk E0-6/C2): `version_in_range` và
`DriverRegistry` (resolution 3 cấp theo D-012 mục 3, cơ chế kế thừa +
override method theo D-012 mục 2).
"""

from __future__ import annotations

import pytest

from app.drivers.base import BaseKlipperDriver, PrinterStatus
from app.drivers.registry import (
    DriverRegistry,
    default_registry,
    resolve_driver,
    version_in_range,
)
from app.moonraker import http_client as hc

HOST = "10.0.0.5"

@pytest.mark.parametrize(
    "firmware_version,version_range,expected",
    [
        ("1.4.2", "*", True),
        ("1.4.2", "", True),
        ("1.4.2", ">=1.3.0", True),
        ("1.2.0", ">=1.3.0", False),
        ("1.2.9", "<1.3.0", True),
        ("1.3.0", "<1.3.0", False),
        ("1.3.0", ">=1.3.0", True),
        ("1.5.0", ">=1.3.0,<2.0.0", True),
        ("2.0.0", ">=1.3.0,<2.0.0", False),
        ("1.3", ">=1.3.0", True),
        ("1.3.0", ">=1.3", True),
    ],
)
def test_version_in_range(
    firmware_version: str, version_range: str, expected: bool
) -> None:
    assert version_in_range(firmware_version, version_range) is expected

def test_version_in_range_invalid_range_raises() -> None:
    with pytest.raises(ValueError):
        version_in_range("1.4.2", "not-a-valid-range")

def test_version_in_range_invalid_firmware_version_raises() -> None:
    with pytest.raises(ValueError):
        version_in_range("not-a-version", ">=1.3.0")

def test_resolve_falls_back_to_base_driver_when_registry_empty() -> None:
    registry = DriverRegistry()
    driver = registry.resolve("QIDI Q1 Pro", "1.4.2", HOST, port=7125)
    assert type(driver) is BaseKlipperDriver
    assert driver.host == HOST
    assert driver.port == 7125

def test_resolve_never_raises_for_unknown_model() -> None:
    """AC gốc: 'không bao giờ báo lỗi không tìm thấy driver' — kể cả
    model hoàn toàn chưa từng đăng ký, hoặc model=None (chưa biết)."""
    registry = DriverRegistry()
    driver = registry.resolve("Model chưa từng nghe tới", "0.0.1", HOST)
    assert type(driver) is BaseKlipperDriver

    driver_unknown = registry.resolve(None, None, HOST)
    assert type(driver_unknown) is BaseKlipperDriver

class _FakeModelDriver(BaseKlipperDriver):
    """Driver giả lập dùng riêng cho test tier (b) — không phải driver
    production cho model QIDI thật (chưa có sai khác thật được ghi nhận
    trong "Ma trận tương thích")."""

def test_tier_b_matches_model_regardless_of_version() -> None:
    registry = DriverRegistry()
    registry.register_for_model("QIDI Q1 Pro", _FakeModelDriver)

    driver_old_fw = registry.resolve("QIDI Q1 Pro", "0.1.0", HOST)
    driver_new_fw = registry.resolve("QIDI Q1 Pro", "9.9.9", HOST)
    assert type(driver_old_fw) is _FakeModelDriver
    assert type(driver_new_fw) is _FakeModelDriver

    other_model_driver = registry.resolve("QIDI Plus3", "1.0.0", HOST)
    assert type(other_model_driver) is BaseKlipperDriver

def test_register_for_model_twice_raises() -> None:
    registry = DriverRegistry()
    registry.register_for_model("QIDI Q1 Pro", _FakeModelDriver)
    with pytest.raises(ValueError):
        registry.register_for_model("QIDI Q1 Pro", _FakeModelDriver)

class _FakeVersionRangeDriver(BaseKlipperDriver):
    """Driver giả lập dùng riêng cho test tier (a)."""

def test_tier_a_takes_priority_over_tier_b() -> None:
    """D-012 mục 3: (a) khớp (model, version_range) ưu tiên hơn (b) khớp
    model bất kể version, khi cả 2 cùng đăng ký cho 1 model."""
    registry = DriverRegistry()
    registry.register_for_model("QIDI Q1 Pro", _FakeModelDriver)
    registry.register_for_version_range(
        "QIDI Q1 Pro", ">=2.0.0", _FakeVersionRangeDriver
    )

    driver_new = registry.resolve("QIDI Q1 Pro", "2.1.0", HOST)
    assert type(driver_new) is _FakeVersionRangeDriver

    driver_old = registry.resolve("QIDI Q1 Pro", "1.0.0", HOST)
    assert type(driver_old) is _FakeModelDriver

def test_tier_a_without_tier_b_falls_back_to_default_outside_range() -> None:
    """Nếu chỉ đăng ký tier (a) (không có tier (b) cho model đó), version
    ngoài range phải rơi thẳng về (c) mặc định, không báo lỗi."""
    registry = DriverRegistry()
    registry.register_for_version_range(
        "QIDI Smart3", ">=1.3.0", _FakeVersionRangeDriver
    )

    driver_in_range = registry.resolve("QIDI Smart3", "1.5.0", HOST)
    assert type(driver_in_range) is _FakeVersionRangeDriver

    driver_out_of_range = registry.resolve("QIDI Smart3", "1.0.0", HOST)
    assert type(driver_out_of_range) is BaseKlipperDriver

class _OverrideGetStatusDriver(BaseKlipperDriver):
    """Driver giả lập mô phỏng 1 model lệch chuẩn CHỈ ở `get_status()`
    (ví dụ model đó trả sai lệch field `print_stats` thật) — chỉ override
    đúng phương thức này, các phương thức khác (upload_and_print,
    cancel_job, ...) kế thừa nguyên vẹn từ `BaseKlipperDriver`, đúng
    D-012 mục 2 ("không viết lại toàn bộ driver")."""

    def get_status(self) -> PrinterStatus:
        return PrinterStatus(
            canonical_status=hc.CANONICAL_ERROR,
            raw_print_stats_state="fake_native_error_state",
            progress_percent=None,
            time_remaining_seconds=None,
            filename=None,
        )

def test_override_single_method_keeps_other_methods_inherited(
    simulator: int,
) -> None:
    registry = DriverRegistry()
    registry.register_for_model("QIDI Model Lệch Chuẩn", _OverrideGetStatusDriver)

    driver = registry.resolve("QIDI Model Lệch Chuẩn", "1.0.0", "127.0.0.1", port=simulator)
    assert type(driver) is _OverrideGetStatusDriver

    status = driver.get_status()
    assert status.canonical_status == hc.CANONICAL_ERROR
    assert status.raw_print_stats_state == "fake_native_error_state"

    upload_result = driver.upload_and_print("benchy.gcode", b"; fake")
    assert upload_result["result"]["print_started"] is True

    info = driver.get_server_info()
    assert info == hc.get_server_info("127.0.0.1", port=simulator)

def test_default_registry_is_isolated_across_tests() -> None:
    """Vì các test khác trong file này tự tạo `DriverRegistry()` riêng
    (không dùng `default_registry`), `default_registry` phải vẫn ở trạng
    thái sạch (chưa đăng ký gì) khi test này chạy — xác nhận không có
    test nào lỡ đăng ký nhầm vào singleton dùng chung."""
    driver = resolve_driver("Bất kỳ model nào", "1.0.0", HOST)
    assert type(driver) is BaseKlipperDriver
    assert default_registry.resolve_driver_class(None, None) is BaseKlipperDriver
