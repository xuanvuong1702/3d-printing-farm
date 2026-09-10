
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

class PrinterRealtimeResponse(BaseModel):

    id: int
    name: str
    realtime_connected: bool
    canonical_status: str
    progress_percent: Optional[int] = None
    time_remaining_seconds: Optional[int] = None
    filename: Optional[str] = None
    extruder_temp: Optional[float] = None
    extruder_target: Optional[float] = None
    bed_temp: Optional[float] = None
    bed_target: Optional[float] = None
    updated_at: Optional[str] = None
