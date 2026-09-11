"""
Test cho `compute_updated_is_held` (E3-4/C1, `app/heartbeat/service.py`)
— xem `docs/State_E3-4_v1.md` mục "Quyết định phạm vi" điểm 2-5 cho
rationale đầy đủ của từng nhánh logic.

Hàm THUẦN — không cần DB/simulator/`heartbeat_db_path`, khác
`tests/heartbeat/test_service.py` (test `run_heartbeat_cycle`, chunk
E1-4/C2). Chunk này (E3-4/C1) CHƯA wiring hàm vào `run_heartbeat_cycle`
— việc đó thuộc chunk C2, sẽ có test tích hợp riêng tại C4.
"""

from __future__ import annotations

from app.heartbeat.service import compute_updated_is_held

def test_finished_with_active_job_sets_hold():
    """Chuyển PRINTING -> FINISHED, có `filename` (job active) -> set
    `is_held = True` (điểm 2+3+4)."""
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="FINISHED",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is True

def test_error_with_active_job_sets_hold():
    """Chuyển PRINTING -> ERROR, có `filename` -> set `is_held = True`
    (điểm 2+3, ERROR cũng thuộc nhóm cần xác nhận)."""
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="ERROR",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is True

def test_finished_without_active_job_does_not_set_hold():
    """Chuyển sang FINISHED nhưng KHÔNG có `filename` (không có job nào
    đang active, ví dụ máy rảnh từ trước) -> KHÔNG set `is_held` (điểm
    2 — điều kiện "máy đang có job active" không thoả)."""
    result = compute_updated_is_held(
        old_status="IDLE",
        new_status="FINISHED",
        is_held=False,
        filename=None,
    )
    assert result is False

def test_status_outside_trigger_set_does_not_set_hold():
    """Chuyển sang một canonical status KHÔNG thuộc {FINISHED, ERROR}
    (ví dụ PAUSED) dù có `filename` -> KHÔNG set `is_held` (điểm 3 —
    AC gốc E3-4 chỉ nêu đúng 2 giá trị, không tự mở rộng)."""
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="PAUSED",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is False

def test_repeated_finished_without_real_transition_does_not_reset_hold_trigger():
    """Máy đứng yên ở FINISHED nhiều vòng heartbeat liên tiếp
    (`old_status == new_status == "FINISHED"`), `is_held` đã là `False`
    (operator vừa xác nhận xong ở vòng trước, chunk C3) -> KHÔNG bị set
    lại `True` ngay vòng kế tiếp dù `filename` vẫn còn (điểm 4 — chỉ
    một SỰ KIỆN chuyển trạng thái thật mới trigger, không phải trạng
    thái đứng yên)."""
    result = compute_updated_is_held(
        old_status="FINISHED",
        new_status="FINISHED",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is False

def test_held_stays_held_when_status_is_offline():
    """Đang `is_held = True`, máy mất kết nối (`new_status = OFFLINE`)
    -> giữ nguyên `True` (không phải ngoại lệ whitelist điểm 5, chỉ
    `PRINTING` với job thật mới tự gỡ được)."""
    result = compute_updated_is_held(
        old_status="FINISHED",
        new_status="OFFLINE",
        is_held=True,
        filename="benchy.gcode",
    )
    assert result is True

def test_held_stays_held_when_printing_without_filename():
    """Đang `is_held = True`, `new_status = PRINTING` nhưng KHÔNG có
    `filename` (dữ liệu bất thường/chưa đủ tin cậy) -> KHÔNG coi là hồi
    phục thật, giữ nguyên `True` (điểm 5 — cả 2 điều kiện phải cùng
    đúng)."""
    result = compute_updated_is_held(
        old_status="ERROR",
        new_status="PRINTING",
        is_held=True,
        filename=None,
    )
    assert result is True

def test_held_auto_clears_on_recovery_to_printing_with_real_job():
    """Đang `is_held = True` (do rớt mạng thoáng qua báo nhầm ERROR),
    máy hồi phục về `PRINTING` với job thật (`filename` có giá trị) ->
    tự gỡ `is_held` về `False` (điểm 5, nguyên tắc #1 CLAUDE.md —
    ngoại lệ whitelist duy nhất)."""
    result = compute_updated_is_held(
        old_status="ERROR",
        new_status="PRINTING",
        is_held=True,
        filename="benchy.gcode",
    )
    assert result is False

def test_not_held_and_printing_with_filename_stays_unheld():
    """`is_held` đã là `False` và máy tiếp tục `PRINTING` bình thường
    -> vẫn `False` (PRINTING không thuộc `_HOLD_TRIGGER_STATUSES`,
    điểm 3)."""
    result = compute_updated_is_held(
        old_status="PRINTING",
        new_status="PRINTING",
        is_held=False,
        filename="benchy.gcode",
    )
    assert result is False
