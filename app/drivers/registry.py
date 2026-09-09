
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
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise ValueError(f"Không parse được version: {version!r}")
    return tuple(int(part) for part in match.group(0).split("."))

def _compare_versions(a: Tuple[int, ...], b: Tuple[int, ...]) -> int:
    pad = max(len(a), len(b))
    a = a + (0,) * (pad - len(a))
    b = b + (0,) * (pad - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0

def version_in_range(firmware_version: str, version_range: str) -> bool:
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

    def __init__(self) -> None:

        self._version_range_registrations: List[_VersionRangeRegistration] = []
        self._model_registrations: Dict[str, Type[PrinterDriver]] = {}

    def register_for_version_range(
        self, model: str, version_range: str, driver_class: Type[PrinterDriver]
    ) -> None:
        self._version_range_registrations.append(
            _VersionRangeRegistration(
                model=model, version_range=version_range, driver_class=driver_class
            )
        )

    def register_for_model(self, model: str, driver_class: Type[PrinterDriver]) -> None:
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
    return default_registry.resolve(
        model, firmware_version, host, port=port, api_key=api_key
    )
