
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module

client = TestClient(main_module.app)

def test_dashboard_renders_base_layout() -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "QIDI Print Farm" in body
    assert 'class="main-nav__item main-nav__item--active"' in body

def test_static_css_is_served() -> None:
    response = client.get("/static/css/style.css")

    assert response.status_code == 200
    assert "--accent-primary" in response.text

def test_dashboard_loads_vendored_htmx_and_alpine() -> None:
    response = client.get("/dashboard")
    body = response.text

    assert "/static/js/vendor/htmx.min.js" in body
    assert "/static/js/vendor/alpine.min.js" in body

def test_vendored_js_files_are_served() -> None:
    htmx_response = client.get("/static/js/vendor/htmx.min.js")
    alpine_response = client.get("/static/js/vendor/alpine.min.js")

    assert htmx_response.status_code == 200
    assert "htmx" in htmx_response.text.lower()
    assert alpine_response.status_code == 200
    assert len(alpine_response.text) > 1000
