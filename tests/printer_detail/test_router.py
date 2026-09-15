
from __future__ import annotations

def test_printer_detail_renders_overview_from_realtime_data(
    client, insert_printer
) -> None:
    printer_id = insert_printer(name="May Chi Tiet A", status="PRINTING")

    response = client.get(f"/printers/{printer_id}/detail")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "May Chi Tiet A" in body
    assert 'class="badge badge--printing">PRINTING' in body

def test_printer_detail_renders_realtime_temps_and_progress(
    client, insert_printer, set_realtime_state
) -> None:
    printer_id = insert_printer(name="May Chi Tiet B", status="PRINTING")
    set_realtime_state(
        printer_id,
        canonical_status="PRINTING",
        progress_percent=55,
        filename="benchy.gcode",
        extruder_temp=210.0,
        bed_temp=60.0,
    )

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    assert "55%" in body
    assert "benchy.gcode" in body
    assert "210" in body
    assert "60" in body

def test_printer_detail_renders_job_history_log(
    client, insert_printer, insert_job_history
) -> None:
    printer_id = insert_printer(name="May Chi Tiet C", status="IDLE")
    insert_job_history(printer_id, filename="vase.gcode", status="finished")
    insert_job_history(printer_id, filename="broken.gcode", status="failed")

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    assert "vase.gcode" in body
    assert 'class="badge badge--finished">finished' in body
    assert "broken.gcode" in body
    assert 'class="badge badge--failed">failed' in body

def test_printer_detail_renders_empty_log_state(client, insert_printer) -> None:
    printer_id = insert_printer(name="May Chi Tiet D", status="IDLE")

    response = client.get(f"/printers/{printer_id}/detail")

    assert response.status_code == 200
    assert "Chưa có lịch sử job nào cho máy này." in response.text

def test_printer_detail_returns_404_when_printer_not_found(client) -> None:
    response = client.get("/printers/999999/detail")

    assert response.status_code == 404

def test_printer_detail_control_buttons_have_correct_hx_post_targets(
    client, insert_printer
) -> None:
    printer_id = insert_printer(name="May Chi Tiet E", status="IDLE")

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    expected_hx_post_targets = [
        f"/printers/{printer_id}/print/start",
        f"/printers/{printer_id}/print/pause",
        f"/printers/{printer_id}/print/resume",
        f"/printers/{printer_id}/print/cancel",
        f"/printers/{printer_id}/emergency_stop",
        f"/printers/{printer_id}/confirm",
        f"/printers/{printer_id}/power/on",
        f"/printers/{printer_id}/power/off",
    ]
    for target in expected_hx_post_targets:
        assert f'hx-post="{target}"' in body

def test_printer_detail_reloads_page_after_successful_action(
    client, insert_printer
) -> None:
    printer_id = insert_printer(name="May Chi Tiet F", status="IDLE")

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert 'hx-swap="none"' in body
    assert "window.location.reload()" in body

def test_dashboard_card_links_to_printer_detail_page(client, insert_printer) -> None:
    printer_id = insert_printer(name="May Link Test", status="IDLE")

    response = client.get("/dashboard")
    body = response.text

    assert response.status_code == 200
    assert f'href="/printers/{printer_id}/detail"' in body
