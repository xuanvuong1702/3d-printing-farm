
from __future__ import annotations

from app.printers.auto_assign import (
    IdleCandidate,
    PrintingCandidate,
    select_auto_assign_printer,
)

def test_tier1_chon_may_co_queue_ngan_nhat() -> None:
    idle_candidates = [
        IdleCandidate(printer_id=1, queue_length=3),
        IdleCandidate(printer_id=2, queue_length=0),
        IdleCandidate(printer_id=3, queue_length=1),
    ]

    result = select_auto_assign_printer(idle_candidates, [])

    assert result == 2

def test_tier1_tie_break_theo_printer_id_khi_queue_bang_nhau() -> None:
    idle_candidates = [
        IdleCandidate(printer_id=5, queue_length=2),
        IdleCandidate(printer_id=2, queue_length=2),
        IdleCandidate(printer_id=8, queue_length=2),
    ]

    result = select_auto_assign_printer(idle_candidates, [])

    assert result == 2

def test_chi_co_tier2_chon_may_time_remaining_nho_nhat() -> None:
    printing_candidates = [
        PrintingCandidate(printer_id=10, time_remaining_seconds=600),
        PrintingCandidate(printer_id=11, time_remaining_seconds=120),
        PrintingCandidate(printer_id=12, time_remaining_seconds=300),
    ]

    result = select_auto_assign_printer([], printing_candidates)

    assert result == 11

def test_tier2_may_thieu_du_lieu_realtime_xep_cuoi() -> None:
    printing_candidates = [
        PrintingCandidate(printer_id=20, time_remaining_seconds=None),
        PrintingCandidate(printer_id=21, time_remaining_seconds=9999),
    ]

    result = select_auto_assign_printer([], printing_candidates)

    assert result == 21

def test_tier2_toan_bo_thieu_du_lieu_tie_break_theo_id() -> None:
    printing_candidates = [
        PrintingCandidate(printer_id=30, time_remaining_seconds=None),
        PrintingCandidate(printer_id=25, time_remaining_seconds=None),
    ]

    result = select_auto_assign_printer([], printing_candidates)

    assert result == 25

def test_khong_co_ung_vien_nao_tra_ve_none() -> None:
    result = select_auto_assign_printer([], [])

    assert result is None

def test_tier1_luon_thang_tier2_du_tier2_sap_xong_hon() -> None:
    idle_candidates = [IdleCandidate(printer_id=1, queue_length=5)]
    printing_candidates = [
        PrintingCandidate(printer_id=2, time_remaining_seconds=1),
    ]

    result = select_auto_assign_printer(idle_candidates, printing_candidates)

    assert result == 1
