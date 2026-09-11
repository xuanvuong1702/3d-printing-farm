"""
Cơ chế resolution driver theo `(model, firmware_version_range)` →
`model` → mặc định (D-012 mục 3 + 4, AC gốc E0-6).

Phạm vi chunk E0-6/C2 — chỉ cơ chế đăng ký/resolution. KHÔNG đăng ký bất
kỳ driver riêng thật nào cho model QIDI cụ thể ở đây — "Ma trận tương
thích" trong `docs/Decisions.md` hiện còn trống (chưa kiểm thử máy QIDI
thật, xem "Rủi ro chung khác"), nên chưa có sai khác thật nào cần
driver riêng. Việc kiểm chứng cơ chế resolution dùng driver giả lập
trong phạm vi test (chunk C3), không phải driver production.

Quyết định áp dụng — D-012 mục 3, thứ tự ưu tiên giảm dần khi chọn
driver cho 1 máy:
  (a) khớp chính xác `(model, firmware_version nằm trong version_range
      đã khai báo)`.
  (b) khớp `model` bất kể version.
  (c) `BaseKlipperDriver` mặc định.
Không bao giờ raise lỗi "không tìm thấy driver" — nhánh (c) luôn tồn
tại (D-012 mục 1: "luôn tồn tại, không bao giờ thiếu").

Định dạng `version_range` khớp với cột "Phạm vi firmware" của "Ma trận
tương thích" trong `Decisions.md`: `>=1.3.0`, `<1.3.0`, `*` (mọi
version), hoặc nhiều điều kiện nối bằng dấu phẩy (AND), ví dụ
`>=1.3.0,<2.0.0`. Không thêm dependency `packaging` (không có sẵn trong
`requirements.txt`) — tự viết so sánh version tối giản, đủ dùng cho
định dạng số nguyên phân tách bằng dấu chấm (`X.Y.Z`, số lượng phần bất
kỳ), khớp đúng những gì Moonraker/Klipper trả về qua `get_printer_info`
(D-007).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Type

from app.drivers.base import BaseKlipperDriver, PrinterDriver
from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT

_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")
_CONSTRAINT_RE = re.compile(r"^(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)$")

_OPERATORS: Dict[str, Callable[[int], bool]] = {
    ">=": lambda cmp: cmp >= 0,
    "<=": lambda cmp: cmp <= 0,
    ">": lambda cmp: cmp > 0,
    "<": lambda cmp: cmp < 0,
    "==": lambda cmp: cmp == 0,
}

def _parse_version(version: str) -> Tuple[int, ...]:
    """`"1.3.0"` -> `(1, 3, 0)`. Chỉ lấy phần số nguyên phân tách bằng
    dấu chấm ở đầu chuỗi (bỏ qua hậu tố dạng `-rc1` nếu có, vì Moonraker/
    Klipper chưa từng trả hậu tố như vậy trong phạm vi đã kiểm chứng)."""
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise ValueError(f"Không parse được version: {version!r}")
    return tuple(int(part) for part in match.group(0).split("."))

def _compare_versions(a: Tuple[int, ...], b: Tuple[int, ...]) -> int:
    """So sánh 2 tuple version, tự đệm 0 cho phần thiếu (vd. (1,3) vs
    (1,3,0) coi là bằng nhau). Trả -1/0/1 kiểu `cmp` cổ điển."""
    pad = max(len(a), len(b))
    a = a + (0,) * (pad - len(a))
    b = b + (0,) * (pad - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0

def version_in_range(firmware_version: str, version_range: str) -> bool:
    """
    True nếu `firmware_version` thoả `version_range`.

    `version_range`: `"*"` (hoặc rỗng) khớp mọi version; nếu không, là 1
    hoặc nhiều điều kiện `<op><version>` (`>=`, `<=`, `>`, `<`, `==`)
    nối bằng dấu phẩy, TẤT CẢ phải thoả (AND) — ví dụ `">=1.3.0,<2.0.0"`.
    """
    version_range = version_range.strip()
    if version_range in ("", "*"):
        return True
    firmware_tuple = _parse_version(firmware_version)
    for raw_part in version_range.split(","):
        part = raw_part.strip()
        if not part:
            continue
        match = _CONSTRAINT_RE.match(part)
        if not match:
            raise ValueError(f"Không parse được version_range: {version_range!r}")
        operator, bound_str = match.groups()
        bound_tuple = _parse_version(bound_str)
        cmp_result = _compare_versions(firmware_tuple, bound_tuple)
        if not _OPERATORS[operator](cmp_result):
            return False
    return True

@dataclass(frozen=True)
class _VersionRangeRegistration:
    model: str
    version_range: str
    driver_class: Type[PrinterDriver]

class DriverRegistry:
    """
    Sổ đăng ký driver theo model/firmware + hiện thực hoá thứ tự
    resolution 3 cấp của D-012 mục 3. Không phải singleton bắt buộc —
    tầng service có thể tạo registry riêng nếu cần (ví dụ test cô lập,
    xem chunk C3), nhưng `default_registry` module-level bên dưới là
    nơi các story sau (E1-1, E3-3, E4-2...) nên dùng chung, để "Ma trận
    tương thích" chỉ cần đăng ký 1 chỗ.
    """

    def __init__(self) -> None:

        self._version_range_registrations: List[_VersionRangeRegistration] = []
        self._model_registrations: Dict[str, Type[PrinterDriver]] = {}

    def register_for_version_range(
        self, model: str, version_range: str, driver_class: Type[PrinterDriver]
    ) -> None:
        """Đăng ký driver riêng cho tier (a): `(model, version_range)`."""
        self._version_range_registrations.append(
            _VersionRangeRegistration(
                model=model, version_range=version_range, driver_class=driver_class
            )
        )

    def register_for_model(self, model: str, driver_class: Type[PrinterDriver]) -> None:
        """Đăng ký driver riêng cho tier (b): khớp `model` bất kể version."""
        if model in self._model_registrations:
            raise ValueError(
                f"Model {model!r} đã có driver đăng ký ở tier (b) "
                "(register_for_model) — mỗi model chỉ được đăng ký 1 driver "
                "tier (b); dùng register_for_version_range nếu cần phân biệt "
                "theo version."
            )
        self._model_registrations[model] = driver_class

    def resolve_driver_class(
        self, model: Optional[str], firmware_version: Optional[str]
    ) -> Type[PrinterDriver]:
        """
        Trả về **class** driver theo đúng thứ tự D-012 mục 3, không bao
        giờ raise lỗi "không tìm thấy driver" (luôn rơi về
        `BaseKlipperDriver` ở tier (c) nếu không khớp gì).
        """
        if model is not None and firmware_version is not None:
            for reg in self._version_range_registrations:
                if reg.model == model and version_in_range(
                    firmware_version, reg.version_range
                ):
                    return reg.driver_class
        if model is not None and model in self._model_registrations:
            return self._model_registrations[model]
        return BaseKlipperDriver

    def resolve(
        self,
        model: Optional[str],
        firmware_version: Optional[str],
        host: str,
        port: int = DEFAULT_MOONRAKER_PORT,
        api_key: Optional[str] = None,
    ) -> PrinterDriver:
        """Resolve class (`resolve_driver_class`) rồi khởi tạo instance
        gắn với đúng 1 máy in (`host`/`port`/`api_key`, khớp chữ ký
        `PrinterDriver.__init__` đã chốt ở chunk C1)."""
        driver_class = self.resolve_driver_class(model, firmware_version)
        return driver_class(host, port=port, api_key=api_key)

default_registry = DriverRegistry()

def resolve_driver(
    model: Optional[str],
    firmware_version: Optional[str],
    host: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: Optional[str] = None,
) -> PrinterDriver:
    """Tiện ích module-level, resolve qua `default_registry`."""
    return default_registry.resolve(
        model, firmware_version, host, port=port, api_key=api_key
    )
