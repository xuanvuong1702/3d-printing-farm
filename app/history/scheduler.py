
from __future__ import annotations

import asyncio
import logging

from app.history.service import run_history_sync_cycle

logger = logging.getLogger(__name__)

SCHEDULER_POLL_INTERVAL_SECONDS = 60.0

async def _history_loop(poll_interval_seconds: float) -> None:
    while True:
        try:
            await asyncio.to_thread(run_history_sync_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:

            logger.exception(
                "Lỗi không mong đợi trong 1 vòng đồng bộ job history - "
                "tiếp tục vòng lặp ở lần kế tiếp."
            )
        await asyncio.sleep(poll_interval_seconds)

def start_history_scheduler(
    poll_interval_seconds: float = SCHEDULER_POLL_INTERVAL_SECONDS,
) -> "asyncio.Task[None]":
    return asyncio.create_task(_history_loop(poll_interval_seconds))

async def stop_history_scheduler(task: "asyncio.Task[None]") -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
