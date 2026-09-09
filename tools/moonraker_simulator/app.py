
from __future__ import annotations

from fastapi import FastAPI

from tools.moonraker_simulator.state import SimulatorState

app = FastAPI(title="QIDI Moonraker Simulator")

state = SimulatorState()
