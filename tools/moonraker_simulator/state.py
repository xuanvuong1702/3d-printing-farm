
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

@dataclass
class SimulatorState:

    webhooks_state: str = "ready"

    print_stats_state: str = "standby"
    print_stats_filename: Optional[str] = None
    print_stats_print_duration: float = 0.0

    virtual_sdcard_progress: float = 0.0
