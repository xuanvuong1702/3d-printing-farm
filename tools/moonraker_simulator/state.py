
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SimulatorState:

    webhooks_state: str = "ready"

    print_stats_state: str = "standby"
    print_stats_filename: Optional[str] = None
    print_stats_print_duration: float = 0.0

    virtual_sdcard_progress: float = 0.0

    extruder_temperature: float = 25.0
    extruder_target: float = 0.0
    heater_bed_temperature: float = 25.0
    heater_bed_target: float = 0.0

    uploaded_files: dict[str, int] = field(default_factory=dict)

    power_devices: dict[str, str] = field(default_factory=lambda: {"printer": "off"})
