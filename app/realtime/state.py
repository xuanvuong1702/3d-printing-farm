
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

SUBSCRIBE_OBJECTS: Dict[str, Optional[list]] = {
    "print_stats": None,
    "virtual_sdcard": None,
    "webhooks": None,
    "extruder": None,
    "heater_bed": None,
}

WS_RECONNECT_INITIAL_BACKOFF_SECONDS = 1.0
WS_RECONNECT_BACKOFF_MULTIPLIER = 2.0
WS_RECONNECT_MAX_BACKOFF_SECONDS = 60.0

@dataclass(frozen=True)
class RealtimePrinterState:

    canonical_status: str
    progress_percent: Optional[int]
    time_remaining_seconds: Optional[int]
    filename: Optional[str]
    extruder_temp: Optional[float]
    extruder_target: Optional[float]
    bed_temp: Optional[float]
    bed_target: Optional[float]
    updated_at: str

class RealtimeStateStore:

    def __init__(self) -> None:
        self._states: Dict[int, RealtimePrinterState] = {}
        self._lock = asyncio.Lock()

    async def set(self, printer_id: int, state: RealtimePrinterState) -> None:
        async with self._lock:
            self._states[printer_id] = state

    async def get(self, printer_id: int) -> Optional[RealtimePrinterState]:
        async with self._lock:
            return self._states.get(printer_id)

    async def get_all(self) -> Dict[int, RealtimePrinterState]:
        async with self._lock:
            return dict(self._states)

    async def delete(self, printer_id: int) -> None:
        async with self._lock:
            self._states.pop(printer_id, None)
