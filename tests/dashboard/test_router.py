
from __future__ import annotations

import pytest

def test_dashboard_renders_base_layout(client) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "QIDI Print Farm" in body
    assert 'class="main-nav__item main-nav__item--active"' in body

def test_static_css_is_served(client) -> None:
    response = client.get("/static/css/style.css")

    assert response.status_code == 200
    assert "--accent-primary" in response.text

def test_dashboard_loads_vendored_htmx_and_alpine(client) -> None:
    response = client.get("/dashboard")
    body = response.text

    assert "/static/js/vendor/htmx.min.js" in body
    assert "/static/js/vendor/alpine.min.js" in body

def test_vendored_js_files_are_served(client) -> None:
    htmx_response = client.get("/static/js/vendor/htmx.min.js")
    alpine_response = client.get("/static/js/vendor/alpine.min.js")

    assert htmx_response.status_code == 200
    assert "htmx" in htmx_response.text.lower()
    assert alpine_response.status_code == 200
    assert len(alpine_response.text) > 1000

def test_dashboard_loads_dashboard_js(client) -> None:
    page_response = client.get("/dashboard")
    assert "/static/js/dashboard.js" in page_response.text

    js_response = client.get("/static/js/dashboard.js")
    assert js_response.status_code == 200
    assert "printerDashboardData" in js_response.text

def test_dashboard_renders_grid_card_with_printer_data(client, insert_printer) -> None:
    insert_printer(name="May In Test A", status="PRINTING")

    response = client.get("/dashboard")

    assert response.status_code == 200
    body = response.text
    assert "May In Test A" in body
    assert "PRINTING" in body

def test_dashboard_renders_empty_state_with_no_printers(client) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Chưa có máy in nào được đăng ký." in response.text

def test_dashboard_embeds_printers_json_for_alpine(client, insert_printer) -> None:
    insert_printer(name="May In Test B", status="IDLE")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "data-printers=" in response.text
    assert "May In Test B" in response.text

def test_dashboard_renders_stale_icon_and_class_when_no_realtime_data(client, insert_printer) -> None:
    insert_printer(name="May In Test C", status="IDLE")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "printer-card--stale" in response.text
    assert "printer-card__stale-icon" in response.text

def test_dashboard_has_connection_banner_and_refresh_button(client) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'class="connection-banner"' in response.text
    assert "reconnectSSE()" in response.text
    assert "Làm mới" in response.text

def test_dashboard_responsive_css_has_three_breakpoints(client) -> None:
    response = client.get("/static/css/style.css")

    assert response.status_code == 200
    body = response.text
    assert "@media (min-width: 640px)" in body
    assert "@media (min-width: 1024px)" in body
    assert "printer-card--stale" in body

_CANONICAL_PRINTER_STATUSES = [
    "IDLE",
    "PRINTING",
    "PAUSED",
    "FINISHED",
    "STOPPED",
    "ERROR",
    "OFFLINE",
    "UNKNOWN",
]

@pytest.mark.parametrize("status", _CANONICAL_PRINTER_STATUSES)
def test_dashboard_renders_correct_badge_class_for_each_canonical_status(
    client, insert_printer, status: str
) -> None:
    insert_printer(name=f"May trang thai {status}", status=status)

    response = client.get("/dashboard")

    assert response.status_code == 200
    body = response.text
    assert f"May trang thai {status}" in body
    assert f'class="badge badge--{status.lower()}">{status}' in body

def test_dashboard_renders_multiple_printers_with_different_statuses_at_once(
    client, insert_printer
) -> None:
    insert_printer(name="May A", status="PRINTING")
    insert_printer(name="May B", status="ERROR")
    insert_printer(name="May C", status="OFFLINE")

    response = client.get("/dashboard")
    body = response.text

    assert response.status_code == 200
    assert "May A" in body and "May B" in body and "May C" in body
    assert 'class="badge badge--printing">PRINTING' in body
    assert 'class="badge badge--error">ERROR' in body
    assert 'class="badge badge--offline">OFFLINE' in body

def test_dashboard_printer_with_realtime_data_is_not_marked_stale(
    client, insert_printer, set_realtime_state
) -> None:
    printer_id = insert_printer(name="May Realtime OK", status="IDLE")
    set_realtime_state(printer_id, canonical_status="PRINTING")

    response = client.get("/dashboard")
    body = response.text

    assert response.status_code == 200
    assert "May Realtime OK" in body

    assert 'class="printer-card">' in body

    name_index = body.index("May Realtime OK")
    card_snippet = body[name_index : name_index + 400]
    assert "printer-card__stale-icon" not in card_snippet

def test_dashboard_mixed_stale_and_fresh_printers_get_distinct_classes(
    client, insert_printer, set_realtime_state
) -> None:
    insert_printer(name="May Stale", status="IDLE")
    fresh_id = insert_printer(name="May Fresh", status="IDLE")
    set_realtime_state(fresh_id, canonical_status="PRINTING")

    response = client.get("/dashboard")
    body = response.text

    assert response.status_code == 200
    assert 'class="printer-card printer-card--stale">' in body
    assert 'class="printer-card">' in body

