
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

@dataclass(frozen=True)
class IdleCandidate:

    printer_id: int
    queue_length: int

@dataclass(frozen=True)
class PrintingCandidate:

    printer_id: int
    time_remaining_seconds: Optional[int]

class NoAvailablePrinterError(Exception):
    pass

def select_auto_assign_printer(
    idle_candidates: List[IdleCandidate],
    printing_candidates: List[PrintingCandidate],
) -> Optional[int]:
    if idle_candidates:
        best_idle = min(
            idle_candidates,
            key=lambda candidate: (candidate.queue_length, candidate.printer_id),
        )
        return best_idle.printer_id

    if printing_candidates:
        best_printing = min(
            printing_candidates,
            key=lambda candidate: (
                candidate.time_remaining_seconds is None,
                (
                    candidate.time_remaining_seconds
                    if candidate.time_remaining_seconds is not None
                    else 0
                ),
                candidate.printer_id,
            ),
        )
        return best_printing.printer_id

    return None
