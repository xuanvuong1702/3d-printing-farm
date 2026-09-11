
from __future__ import annotations

import json
import logging
import sqlite3
from typing import List

from app.db.migrate import DEFAULT_DB_PATH
from app.drivers import resolve_driver
from app.moonraker.http_client import MoonrakerClientError

logger = logging.getLogger(__name__)

_SELECT_CANDIDATE_PRINTERS_SQL = """
SELECT id, ip, moonraker_port, model, api_key, klipper_version, capabilities
FROM printers
WHERE status = 'IDLE' AND is_held = 0
"""

_SELECT_NEXT_QUEUED_JOB_SQL = """
SELECT id, filename
FROM jobs
WHERE printer_id = ? AND status = 'queued'
ORDER BY priority DESC, created_at ASC
LIMIT 1
"""

_CLAIM_JOB_SQL = """
UPDATE jobs
SET status = 'printing', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ? AND status = 'queued'
"""

_MARK_JOB_FAILED_SQL = """
UPDATE jobs
SET status = 'failed', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?
"""

def run_dispatch_cycle(db_path: str = DEFAULT_DB_PATH) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        candidate_rows = connection.execute(_SELECT_CANDIDATE_PRINTERS_SQL).fetchall()

        for (
            printer_id,
            ip,
            moonraker_port,
            model,
            api_key,
            klipper_version,
            capabilities_json,
        ) in candidate_rows:
            capabilities: List[str] = json.loads(capabilities_json)
            if "job_queue" in capabilities:

                continue

            job_row = connection.execute(
                _SELECT_NEXT_QUEUED_JOB_SQL, (printer_id,)
            ).fetchone()
            if job_row is None:
                continue

            job_id, filename = job_row

            cursor = connection.execute(_CLAIM_JOB_SQL, (job_id,))
            connection.commit()
            if cursor.rowcount != 1:

                continue

            driver = resolve_driver(
                model=model,
                firmware_version=klipper_version,
                host=ip,
                port=moonraker_port,
                api_key=api_key,
            )

            try:
                driver.start_uploaded_print(filename)
            except MoonrakerClientError:
                connection.execute(_MARK_JOB_FAILED_SQL, (job_id,))
                connection.commit()
                logger.exception(
                    "Lỗi khi bắt đầu in job id=%s trên máy id=%s - đánh dấu "
                    "failed, tiếp tục vòng dispatch cho máy khác.",
                    job_id,
                    printer_id,
                )
    finally:
        connection.close()
