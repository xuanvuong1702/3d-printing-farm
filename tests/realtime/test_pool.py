
from __future__ import annotations

import asyncio
import warnings

from app.realtime.pool import WebsocketPool
from app.realtime.state import RealtimeStateStore
from app.moonraker.http_client import CANONICAL_IDLE

import tools.moonraker_simulator.app as simulator_app

_NOTIFICATION_SETTLE_SECONDS = 0.3

_RECONNECT_MAX_WAIT_SECONDS = 8.0
_RECONNECT_POLL_INTERVAL_SECONDS = 0.2

def test_pool_connects_to_multiple_printers_independently(
    simulator_factory, insert_printer, realtime_db_path
) -> None:
    handle_a = simulator_factory(host="127.0.0.1")
    handle_b = simulator_factory(host="127.0.0.2")
    printer_a = insert_printer(handle_a.host, handle_a.port, name="Máy A")
    printer_b = insert_printer(handle_b.host, handle_b.port, name="Máy B")

    async def _scenario(db_path: str):
        store = RealtimeStateStore()
        pool = WebsocketPool(store=store, db_path=db_path)
        pool.start()
        try:
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)
            state_a = await store.get(printer_a)
            state_b = await store.get(printer_b)
            assert state_a is not None
            assert state_b is not None
            assert state_a.canonical_status == CANONICAL_IDLE
            assert state_b.canonical_status == CANONICAL_IDLE
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                await pool.stop()

    asyncio.run(_scenario(realtime_db_path))

def test_pool_one_printer_failure_does_not_affect_others(
    simulator_factory, insert_printer, realtime_db_path
) -> None:
    handle_ok = simulator_factory(host="127.0.0.1")
    printer_ok = insert_printer(handle_ok.host, handle_ok.port, name="Máy khoẻ")

    handle_broken = simulator_factory(host="127.0.0.2")
    broken_port = handle_broken.port
    handle_broken.stop()
    printer_broken = insert_printer(handle_broken.host, broken_port, name="Máy lỗi")

    async def _scenario(db_path: str):
        store = RealtimeStateStore()
        pool = WebsocketPool(store=store, db_path=db_path)
        pool.start()
        try:
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)
            state_ok = await store.get(printer_ok)
            state_broken = await store.get(printer_broken)
            assert state_ok is not None
            assert state_ok.canonical_status == CANONICAL_IDLE
            assert state_broken is None
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                await pool.stop()

    asyncio.run(_scenario(realtime_db_path))

def test_pool_handles_five_printers_independently(
    simulator_factory, insert_printer, realtime_db_path
) -> None:
    handles = [simulator_factory(host=f"127.0.0.{i}") for i in range(1, 6)]
    printer_ids = [
        insert_printer(h.host, h.port, name=f"Máy {i}")
        for i, h in enumerate(handles, start=1)
    ]

    async def _scenario(db_path: str):
        store = RealtimeStateStore()
        pool = WebsocketPool(store=store, db_path=db_path)
        pool.start()
        try:
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)
            for printer_id in printer_ids:
                current = await store.get(printer_id)
                assert current is not None
                assert current.canonical_status == CANONICAL_IDLE
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                await pool.stop()

    asyncio.run(_scenario(realtime_db_path))

def test_pool_reconnects_after_simulator_restart(
    simulator_factory, insert_printer, realtime_db_path
) -> None:
    handle = simulator_factory()
    printer_id = insert_printer(handle.host, handle.port)

    async def _scenario(db_path: str):
        store = RealtimeStateStore()
        pool = WebsocketPool(store=store, db_path=db_path)
        pool.start()
        try:
            await asyncio.sleep(_NOTIFICATION_SETTLE_SECONDS)
            assert await store.get(printer_id) is not None

            handle.restart()

            simulator_app.state.extruder_temperature = 123.0
            reconnected = False
            elapsed = 0.0
            while elapsed < _RECONNECT_MAX_WAIT_SECONDS:
                await asyncio.sleep(_RECONNECT_POLL_INTERVAL_SECONDS)
                elapsed += _RECONNECT_POLL_INTERVAL_SECONDS

                simulator_app.state.extruder_temperature = 123.0
                current = await store.get(printer_id)
                if current is not None and current.extruder_temp == 123.0:
                    reconnected = True
                    break
            assert reconnected is True
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                await pool.stop()

    asyncio.run(_scenario(realtime_db_path))
