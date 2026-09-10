
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from moonraker_api import MoonrakerClient, MoonrakerListener

from app.db.migrate import DEFAULT_DB_PATH
from app.moonraker.http_client import (
    CANONICAL_OFFLINE,
    CANONICAL_PAUSED,
    CANONICAL_PRINTING,
    _map_print_stats_state,
)
from app.realtime.state import (
    SUBSCRIBE_OBJECTS,
    RealtimePrinterState,
    RealtimeStateStore,
)

_LOGGER = logging.getLogger(__name__)

_MIN_PROGRESS_FOR_ESTIMATE = 0.02

_SELECT_PRINTER_SQL = "SELECT ip, moonraker_port, api_key FROM printers WHERE id = ?"

def fetch_printer_connection_params(
    printer_id: int, db_path: str = DEFAULT_DB_PATH
) -> tuple:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(_SELECT_PRINTER_SQL, (printer_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"Không tìm thấy máy in id={printer_id} trong DB")
    host, port, api_key = row
    return host, port, api_key

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _compute_state(raw_status: Dict[str, Dict[str, Any]]) -> RealtimePrinterState:
    print_stats = raw_status.get("print_stats", {})
    virtual_sdcard = raw_status.get("virtual_sdcard", {})
    webhooks = raw_status.get("webhooks", {})
    extruder = raw_status.get("extruder", {})
    heater_bed = raw_status.get("heater_bed", {})

    webhooks_state = webhooks.get("state")
    raw_print_state = print_stats.get("state")

    if webhooks_state != "ready":
        canonical_status = CANONICAL_OFFLINE
    else:
        canonical_status = _map_print_stats_state(raw_print_state)

    pct = virtual_sdcard.get("progress")
    elapsed = print_stats.get("print_duration")

    progress_percent: Optional[int] = None
    time_remaining_seconds: Optional[int] = None
    if pct is not None:
        if pct > _MIN_PROGRESS_FOR_ESTIMATE:
            progress_percent = round(pct * 100)
            if elapsed is not None:
                time_remaining_seconds = round(elapsed * (1 - pct) / pct)
        else:
            progress_percent = round(pct * 100)

    filename = None
    if canonical_status in (CANONICAL_PRINTING, CANONICAL_PAUSED):
        filename = print_stats.get("filename")

    return RealtimePrinterState(
        canonical_status=canonical_status,
        progress_percent=progress_percent,
        time_remaining_seconds=time_remaining_seconds,
        filename=filename,
        extruder_temp=_to_optional_float(extruder.get("temperature")),
        extruder_target=_to_optional_float(extruder.get("target")),
        bed_temp=_to_optional_float(heater_bed.get("temperature")),
        bed_target=_to_optional_float(heater_bed.get("target")),
        updated_at=_now_iso(),
    )

class PrinterWebsocketConnection(MoonrakerListener):

    def __init__(
        self,
        printer_id: int,
        host: str,
        port: int,
        api_key: Optional[str],
        store: RealtimeStateStore,
    ) -> None:
        self.printer_id = printer_id
        self.store = store
        self._raw_status: Dict[str, Dict[str, Any]] = {}
        self._client = MoonrakerClient(listener=self, host=host, port=port, api_key=api_key)

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    async def connect(self) -> bool:
        connected = await self._client.connect()
        if not connected:
            return False
        response = await self._client.call_method(
            "printer.objects.subscribe", objects=SUBSCRIBE_OBJECTS
        )
        initial_status = (response or {}).get("status") or {}
        if initial_status:
            await self._handle_status_delta(initial_status)
        return True

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def on_notification(self, method: str, data: Any) -> None:
        if method != "notify_status_update":
            return
        if not data:
            return
        status_delta = data[0] if isinstance(data, list) else data
        if not isinstance(status_delta, dict):
            return
        await self._handle_status_delta(status_delta)

    async def state_changed(self, state: str) -> None:
        _LOGGER.debug("Printer %s websocket state -> %s", self.printer_id, state)

    async def on_exception(self, exception: Any) -> None:
        _LOGGER.warning(
            "Printer %s websocket exception: %s", self.printer_id, exception
        )

    async def _handle_status_delta(
        self, status_delta: Dict[str, Dict[str, Any]]
    ) -> None:
        for obj_name, obj_fields in status_delta.items():
            if not isinstance(obj_fields, dict):
                continue
            self._raw_status.setdefault(obj_name, {}).update(obj_fields)
        state = _compute_state(self._raw_status)
        await self.store.set(self.printer_id, state)
