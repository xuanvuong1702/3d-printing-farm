
from __future__ import annotations

import asyncio

import aiohttp

from app.realtime.connection import PrinterWebsocketConnection
from app.realtime.state import RealtimeStateStore
from app.moonraker.http_client import CANONICAL_IDLE, CANONICAL_PRINTING

import tools.moonraker_simulator.app as simulator_app

_NOTIFICATION_SETTLE_SECONDS = 0.3

async def _connect(handle) -> tuple:
    store = RealtimeStateStore()
    session = aiohttp.ClientSession()
    connection = PrinterWebsocketConnection(
        printer_id=1,
        host=handle.host,
        port=handle.port,
        api_key=None,
        store=store,
        session=session,
    )
    connected = await connection.connect()
    assert connected is True
    return connection, store, session

def test_connect_subscribe_receives_initial_snapshot(simulator_factory) -> None:
    handle = simulator_factory()

    async def _scenario():
        connection, store, session = await _connect(handle)
        try:
            result = await store.get(1)
            assert result is not None
            assert result.canonical_status == CANONICAL_IDLE
            assert result.progress_percent == 0
            assert result.filename is None
            assert result.extruder_temp == 25.0
            assert result.extruder_target == 0.0
            assert result.bed_temp == 25.0
            assert result.bed_target == 0.0
        finally:
            await connection.disconnect()
            await session.close()

    asyncio.run(_scenario())

def test_notification_updates_store(simulator_factory) -> None:
    handle = simulator_factory()

    async def _scenario():
        connection, store, session = await _connect(handle)
        try:
            simulator_app.state.print_stats_state = "printing"
            simulator_app.state.print_stats_filename = "benchy.gcode"
            simulator_app.state.virtual_sdcard_progress = 0.5
            simulator_app.state.print_stats_print_duration = 100.0
            simulator_app.state.extruder_temperature = 200.0
            simulator_app.state.extruder_target = 210.0
            simulator_app.state.heater_bed_temperature = 60.0
            simulator_app.state.heater_bed_target = 60.0

            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)

            result = await store.get(1)
            assert result.canonical_status == CANONICAL_PRINTING
            assert result.progress_percent == 50

            assert result.time_remaining_seconds == 100
            assert result.filename == "benchy.gcode"
            assert result.extruder_temp == 200.0
            assert result.extruder_target == 210.0
            assert result.bed_temp == 60.0
            assert result.bed_target == 60.0
        finally:
            await connection.disconnect()
            await session.close()

    asyncio.run(_scenario())

def test_partial_notification_delta_preserves_other_fields(simulator_factory) -> None:
    handle = simulator_factory()

    async def _scenario():
        connection, store, session = await _connect(handle)
        try:
            simulator_app.state.print_stats_state = "printing"
            simulator_app.state.print_stats_filename = "cube.gcode"
            simulator_app.state.virtual_sdcard_progress = 0.5
            simulator_app.state.print_stats_print_duration = 100.0
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)

            first = await store.get(1)
            assert first.progress_percent == 50
            assert first.filename == "cube.gcode"
            assert first.extruder_temp == 25.0

            simulator_app.state.extruder_temperature = 205.0
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)

            second = await store.get(1)
            assert second.extruder_temp == 205.0

            assert second.progress_percent == 50
            assert second.filename == "cube.gcode"
            assert second.canonical_status == CANONICAL_PRINTING
        finally:
            await connection.disconnect()
            await session.close()

    asyncio.run(_scenario())
