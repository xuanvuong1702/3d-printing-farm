
from __future__ import annotations

from app.spools.service import build_spool_report

def _spoolman_response(
    *,
    material: str = "PLA",
    filament_name: str = "Red",
    used_weight: float = 100.0,
    remaining_weight: float = 900.0,
) -> dict:
    return {
        "response": {
            "filament": {"material": material, "name": filament_name},
            "used_weight": used_weight,
            "remaining_weight": remaining_weight,
        },
        "error": None,
    }

def test_spool_with_multiple_jobs_returns_all_of_them(
    conn, insert_job_history
) -> None:
    printer_id, jh_id_1 = insert_job_history(
        status="finished",
        filename="a.gcode",
        start_time="2026-01-05T10:00:00Z",
        spool_id="sp1",
    )
    _, jh_id_2 = insert_job_history(
        printer_id=printer_id,
        status="failed",
        filename="b.gcode",
        start_time="2026-01-06T10:00:00Z",
        spool_id="sp1",
    )

    result = build_spool_report(
        conn, printer_id, "sp1", _spoolman_response()
    )

    assert {entry.job_history_id for entry in result.jobs} == {jh_id_1, jh_id_2}
    entry_by_id = {entry.job_history_id: entry for entry in result.jobs}
    assert entry_by_id[jh_id_1].filename == "a.gcode"
    assert entry_by_id[jh_id_1].status == "finished"
    assert entry_by_id[jh_id_1].start_time == "2026-01-05T10:00:00Z"
    assert entry_by_id[jh_id_2].filename == "b.gcode"
    assert entry_by_id[jh_id_2].status == "failed"

def test_spool_with_no_matching_job_history_returns_empty_jobs(conn) -> None:
    result = build_spool_report(conn, 1, "khong_ton_tai", _spoolman_response())

    assert result.jobs == []

def test_jobs_filtered_by_printer_id_excludes_other_printer(
    conn, insert_job_history
) -> None:
    printer_a, jh_id_a = insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        spool_id="sp1",
    )
    insert_job_history(
        status="finished",
        start_time="2026-01-05T10:00:00Z",
        spool_id="sp1",
    )

    result = build_spool_report(conn, printer_a, "sp1", _spoolman_response())

    assert len(result.jobs) == 1
    assert result.jobs[0].job_history_id == jh_id_a

def test_missing_filament_and_weight_fields_stay_none(conn) -> None:
    spoolman_response = {"response": {}, "error": None}

    result = build_spool_report(conn, 1, "sp1", spoolman_response)

    assert result.material is None
    assert result.filament_name is None
    assert result.used_weight_g is None
    assert result.remaining_weight_g is None

def test_filament_present_but_material_null_stays_none(conn) -> None:
    spoolman_response = {
        "response": {
            "filament": {"material": None, "name": None},
            "used_weight": 50.0,
            "remaining_weight": 950.0,
        },
        "error": None,
    }

    result = build_spool_report(conn, 1, "sp1", spoolman_response)

    assert result.material is None
    assert result.filament_name is None
    assert result.used_weight_g == 50.0
    assert result.remaining_weight_g == 950.0

def test_response_fields_populate_correctly_when_present(conn) -> None:
    result = build_spool_report(
        conn,
        1,
        "sp1",
        _spoolman_response(
            material="PETG",
            filament_name="Galaxy Black",
            used_weight=250.5,
            remaining_weight=749.5,
        ),
    )

    assert result.spool_id == "sp1"
    assert result.printer_id == 1
    assert result.material == "PETG"
    assert result.filament_name == "Galaxy Black"
    assert result.used_weight_g == 250.5
    assert result.remaining_weight_g == 749.5
