
from __future__ import annotations

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

