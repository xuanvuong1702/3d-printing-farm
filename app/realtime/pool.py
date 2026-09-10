
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Dict, List, Optional, Tuple

import aiohttp

from app.db.migrate import DEFAULT_DB_PATH
from app.realtime.connection import PrinterWebsocketConnection
from app.realtime.state import (
    WS_RECONNECT_BACKOFF_MULTIPLIER,
    WS_RECONNECT_INITIAL_BACKOFF_SECONDS,
    WS_RECONNECT_MAX_BACKOFF_SECONDS,
    RealtimeStateStore,
)

_LOGGER = logging.getLogger(__name__)

_SELECT_ALL_PRINTERS_SQL = "SELECT id, ip, moonraker_port, api_key FROM printers"

def _fetch_all_printers(
    db_path: str = DEFAULT_DB_PATH,
) -> List[Tuple[int, str, int, Optional[str]]]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(_SELECT_ALL_PRINTERS_SQL).fetchall()
    finally:
        connection.close()
    return rows

def _compute_backoff_seconds(consecutive_failures: int) -> float:
    backoff = WS_RECONNECT_INITIAL_BACKOFF_SECONDS * (
        WS_RECONNECT_BACKOFF_MULTIPLIER**consecutive_failures
    )
    return min(backoff, WS_RECONNECT_MAX_BACKOFF_SECONDS)

async def _run_printer_connection(
    printer_id: int,
    host: str,
    port: int,
    api_key: Optional[str],
    store: RealtimeStateStore,
) -> None:
    session = aiohttp.ClientSession()
    connection = PrinterWebsocketConnection(
        printer_id=printer_id,
        host=host,
        port=port,
        api_key=api_key,
        store=store,
        session=session,
    )
    consecutive_failures = 0
    try:
        while True:
            try:
                connected = await connection.connect()
            except asyncio.CancelledError:
                raise
            except Exception as error:

                connected = False
                _LOGGER.warning(
                    "Printer %s: lỗi khi connect(): %s", printer_id, error
                )

            if not connected:
                consecutive_failures += 1
                backoff_seconds = _compute_backoff_seconds(consecutive_failures)
                _LOGGER.info(
                    "Printer %s: connect() thất bại (lần %d liên tiếp), "
                    "thử lại sau %.1fs",
                    printer_id,
                    consecutive_failures,
                    backoff_seconds,
                )
                await asyncio.sleep(backoff_seconds)
                continue

            consecutive_failures = 0

            await connection.disconnected.wait()
            _LOGGER.info(
                "Printer %s: mất kết nối WS, sẽ backoff rồi reconnect",
                printer_id,
            )
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await connection.disconnect()
        except Exception:

            _LOGGER.debug(
                "Printer %s: lỗi khi disconnect() lúc dọn dẹp (bỏ qua)",
                printer_id,
                exc_info=True,
            )
        await session.close()

class WebsocketPool:

    def __init__(self, store: RealtimeStateStore, db_path: str = DEFAULT_DB_PATH) -> None:
        self.store = store
        self.db_path = db_path
        self._tasks: Dict[int, "asyncio.Task[None]"] = {}

    def start(self) -> None:
        try:
            printers = _fetch_all_printers(self.db_path)
        except sqlite3.Error as error:
            _LOGGER.warning(
                "WebsocketPool: không đọc được danh sách máy từ DB (%s) - "
                "khởi động với 0 kết nối, sẽ cần restart service để pool "
                "nhận máy sau khi DB sẵn sàng (Quyết định 7, không "
                "hot-reload)",
                error,
            )
            printers = []
        for printer_id, host, port, api_key in printers:
            self._tasks[printer_id] = asyncio.create_task(
                _run_printer_connection(printer_id, host, port, api_key, self.store)
            )
        _LOGGER.info("WebsocketPool: khởi động %d kết nối", len(self._tasks))

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

def start_websocket_pool(
    store: RealtimeStateStore, db_path: str = DEFAULT_DB_PATH
) -> WebsocketPool:
    pool = WebsocketPool(store=store, db_path=db_path)
    pool.start()
    return pool

async def stop_websocket_pool(pool: WebsocketPool) -> None:
    await pool.stop()
