
from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient

def test_log_tab_table_wrapped_in_table_scroll(
    client: TestClient,
    insert_printer: Callable[..., int],
    insert_job_history: Callable[..., int],
) -> None:
    printer_id = insert_printer()
    insert_job_history(printer_id)

    response = client.get(f"/printers/{printer_id}/detail")

    assert response.status_code == 200
    html = response.text
    assert 'class="table-scroll"' in html

    log_tab_start = html.index("tab === 'log'")
    table_scroll_pos = html.index('class="table-scroll"', log_tab_start)
    kv_table_pos = html.index('class="kv-table"', table_scroll_pos)
    assert table_scroll_pos < kv_table_pos

def test_overview_tab_table_not_wrapped_in_table_scroll(
    client: TestClient,
    insert_printer: Callable[..., int],
) -> None:
    printer_id = insert_printer()

    response = client.get(f"/printers/{printer_id}/detail")

    assert response.status_code == 200
    html = response.text
    overview_tab_start = html.index("tab === 'overview'")
    log_tab_start = html.index("tab === 'log'")
    overview_section = html[overview_tab_start:log_tab_start]
    assert 'class="table-scroll"' not in overview_section
