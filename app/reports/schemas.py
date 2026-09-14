
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

class WeeklyProductionEntry(BaseModel):

    printer_id: int
    iso_year: int
    iso_week: int
    finished_job_count: int

class ReportSummaryResponse(BaseModel):

    total_run_hours: float
    error_rate: Optional[float] = None
    weekly_production: List[WeeklyProductionEntry]
