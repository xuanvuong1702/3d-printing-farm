
from __future__ import annotations

import httpx
import pytest
import respx

from app.moonraker import http_client as hc

HOST = "127.0.0.1"
PORT = hc.DEFAULT_MOONRAKER_PORT
BASE_URL = f"http://{HOST}:{PORT}"

def _objects_query_result(
    *,
    webhooks_state: str = "ready",
    print_stats_state: str | None = "printing",
    progress: float | None = None,
    print_duration: float | None = None,
    filename: str | None = None,
) -> dict:
    print_stats: dict = {}
    if print_stats_state is not None:
        print_stats["state"] = print_stats_state
    if print_duration is not None:
        print_stats["print_duration"] = print_duration
    if filename is not None:
        print_stats["filename"] = filename

    virtual_sdcard: dict = {}
    if progress is not None:
        virtual_sdcard["progress"] = progress

    return {
        "result": {
            "status": {
                "webhooks": {"state": webhooks_state},
                "print_stats": print_stats,
                "virtual_sdcard": virtual_sdcard,
            }
        }
    }

@pytest.mark.parametrize(
    "raw_state,expected_canonical",
    [
        ("standby", hc.CANONICAL_IDLE),
        ("printing", hc.CANONICAL_PRINTING),
        ("paused", hc.CANONICAL_PAUSED),
        ("complete", hc.CANONICAL_FINISHED),
        ("error", hc.CANONICAL_ERROR),
        ("cancelled", hc.CANONICAL_STOPPED),
        ("some_unheard_of_state", hc.CANONICAL_UNKNOWN),
    ],
)
@respx.mock
def test_get_status_maps_print_stats_state_to_canonical(raw_state, expected_canonical):
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="ready", print_stats_state=raw_state
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.canonical_status == expected_canonical
    assert result.raw_print_stats_state == raw_state

@respx.mock
def test_get_status_offline_when_webhooks_not_ready_overrides_printing():
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="startup", print_stats_state="printing"
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.canonical_status == hc.CANONICAL_OFFLINE

@respx.mock
def test_get_status_offline_when_webhooks_not_ready_overrides_error():
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="shutdown", print_stats_state="error"
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.canonical_status == hc.CANONICAL_OFFLINE

@respx.mock
def test_get_status_progress_at_exact_boundary_no_time_remaining():
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="ready",
            print_stats_state="printing",
            progress=0.02,
            print_duration=100.0,
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.progress_percent == 2
    assert result.time_remaining_seconds is None

@respx.mock
def test_get_status_progress_zero():
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="ready",
            print_stats_state="printing",
            progress=0.0,
            print_duration=0.0,
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.progress_percent == 0
    assert result.time_remaining_seconds is None

@respx.mock
def test_get_status_progress_above_threshold_computes_time_remaining():
    pct = 0.25
    elapsed = 100.0
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="ready",
            print_stats_state="printing",
            progress=pct,
            print_duration=elapsed,
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.progress_percent == round(pct * 100)
    assert result.time_remaining_seconds == round(elapsed * (1 - pct) / pct)

@respx.mock
def test_get_status_filename_only_when_printing_or_paused():
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="ready",
            print_stats_state="printing",
            filename="benchy.gcode",
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.filename == "benchy.gcode"

@respx.mock
def test_get_status_filename_none_when_idle():
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="ready",
            print_stats_state="standby",
            filename="leftover_from_last_job.gcode",
        ),
    )

    result = hc.get_status(HOST, port=PORT)

    assert result.canonical_status == hc.CANONICAL_IDLE
    assert result.filename is None

@respx.mock
def test_gcode_script_sends_script_as_query_param():
    route = respx.post(f"{BASE_URL}/printer/gcode/script").respond(
        200, json={"result": "ok"}
    )

    result = hc.gcode_script(HOST, "G28", port=PORT)

    assert route.called
    sent_request = route.calls.last.request
    assert sent_request.url.params.get("script") == "G28"

    assert sent_request.content == b""
    assert result == {"result": "ok"}

@respx.mock
def test_gcode_script_passes_through_arbitrary_script_text():
    route = respx.post(f"{BASE_URL}/printer/gcode/script").respond(
        200, json={"result": "ok"}
    )

    hc.gcode_script(HOST, "M117 Hello World", port=PORT)

    sent_request = route.calls.last.request
    assert sent_request.url.params.get("script") == "M117 Hello World"

@respx.mock
def test_upload_and_print_sends_print_as_form_field_not_query_param():
    route = respx.post(f"{BASE_URL}/server/files/upload").respond(
        200, json={"result": {"item": {"path": "gcodes/benchy.gcode"}}}
    )

    result = hc.upload_and_print(HOST, "benchy.gcode", b"G28\nG1 X10\n", port=PORT)

    assert route.called
    sent_request = route.calls.last.request

    assert "print" not in sent_request.url.params

    body = sent_request.content
    assert b'name="print"' in body
    assert b"true" in body

    assert b'name="file"' in body
    assert b"benchy.gcode" in body

    assert result == {"result": {"item": {"path": "gcodes/benchy.gcode"}}}

@pytest.mark.parametrize(
    "func,path",
    [
        (hc.cancel_job, "/printer/print/cancel"),
        (hc.pause_job, "/printer/print/pause"),
        (hc.resume_job, "/printer/print/resume"),
    ],
)
@respx.mock
def test_job_control_endpoints_call_correct_path_and_method(func, path):
    route = respx.post(f"{BASE_URL}{path}").respond(200, json={"result": "ok"})

    result = func(HOST, port=PORT)

    assert route.called
    sent_request = route.calls.last.request
    assert sent_request.method == "POST"
    assert sent_request.content == b""
    assert result == {"result": "ok"}

@pytest.mark.parametrize(
    "raw_state,expected",
    [
        ("printing", True),
        ("paused", True),
        ("standby", False),
        ("complete", False),
        ("error", False),
        ("cancelled", False),
    ],
)
@respx.mock
def test_check_if_printing(raw_state, expected):
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="ready", print_stats_state=raw_state
        ),
    )

    assert hc.check_if_printing(HOST, port=PORT) is expected

@respx.mock
def test_check_if_printing_false_when_offline():
    respx.get(f"{BASE_URL}/printer/objects/query").respond(
        200,
        json=_objects_query_result(
            webhooks_state="startup", print_stats_state="printing"
        ),
    )

    assert hc.check_if_printing(HOST, port=PORT) is False

@respx.mock
def test_request_sends_api_key_header_when_provided():
    route = respx.get(f"{BASE_URL}/server/info").respond(
        200, json={"result": {"klippy_connected": True}}
    )

    hc.get_server_info(HOST, port=PORT, api_key="secret-key-123")

    sent_request = route.calls.last.request
    assert sent_request.headers.get("X-Api-Key") == "secret-key-123"

@respx.mock
def test_request_omits_api_key_header_when_none():
    route = respx.get(f"{BASE_URL}/server/info").respond(
        200, json={"result": {"klippy_connected": True}}
    )

    hc.get_server_info(HOST, port=PORT, api_key=None)

    sent_request = route.calls.last.request
    assert "X-Api-Key" not in sent_request.headers

@respx.mock
def test_request_raises_moonraker_client_error_on_http_error_status():
    respx.get(f"{BASE_URL}/server/info").respond(500, text="internal error")

    with pytest.raises(hc.MoonrakerClientError):
        hc.get_server_info(HOST, port=PORT)

@respx.mock
def test_request_raises_moonraker_client_error_on_network_error():
    respx.get(f"{BASE_URL}/server/info").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with pytest.raises(hc.MoonrakerClientError):
        hc.get_server_info(HOST, port=PORT)

@respx.mock
def test_get_printer_info_returns_result_payload():
    respx.get(f"{BASE_URL}/printer/info").respond(
        200, json={"result": {"software_version": "v0.11.0"}}
    )

    result = hc.get_printer_info(HOST, port=PORT)

    assert result == {"software_version": "v0.11.0"}
