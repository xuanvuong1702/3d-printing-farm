
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

class SpoolJobEntry(BaseModel):

    job_history_id: int
    job_id: int
    filename: str
    status: str
    start_time: Optional[str] = None

class SpoolReport(BaseModel):

    spool_id: str
    printer_id: int
    material: Optional[str] = None
    filament_name: Optional[str] = None
    used_weight_g: Optional[float] = None
    remaining_weight_g: Optional[float] = None
    jobs: List[SpoolJobEntry]
