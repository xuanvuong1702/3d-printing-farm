
from __future__ import annotations

import json

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

@respx.mock
def test_upload_file_sends_print_false_as_form_field():
    route = respx.post(f"{BASE_URL}/server/files/upload").respond(
        200, json={"result": {"item": {"path": "gcodes/plate.gcode"}}}
    )

    result = hc.upload_file(HOST, "plate.gcode", b"; fake gcode content", port=PORT)

    assert route.called
    sent_request = route.calls.last.request

    assert "print" not in sent_request.url.params

    body = sent_request.content
    assert b'name="print"' in body
    assert b"false" in body
    assert b'name="file"' in body
    assert b"plate.gcode" in body

    assert result == {"result": {"item": {"path": "gcodes/plate.gcode"}}}

@respx.mock
def test_get_file_metadata_sends_filename_as_query_param():
    route = respx.get(f"{BASE_URL}/server/files/metadata").respond(
        200, json={"size": 12345, "estimated_time": 678, "filename": "plate.gcode"}
    )

    result = hc.get_file_metadata(HOST, "plate.gcode", port=PORT)

    assert route.called
    sent_request = route.calls.last.request
    assert sent_request.url.params.get("filename") == "plate.gcode"

    assert result == {"size": 12345, "estimated_time": 678, "filename": "plate.gcode"}

@respx.mock
def test_get_file_metadata_raises_on_404():
    respx.get(f"{BASE_URL}/server/files/metadata").respond(
        404, json={"error": {"message": "File không tồn tại"}}
    )

    with pytest.raises(hc.MoonrakerClientError):
        hc.get_file_metadata(HOST, "khong_ton_tai.gcode", port=PORT)

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
def test_get_job_queue_status_returns_flat_response_not_wrapped():
    route = respx.get(f"{BASE_URL}/server/job_queue/status").respond(
        200,
        json={
            "queued_jobs": [{"filename": "a.gcode"}, {"filename": "b.gcode"}],
            "queue_state": "ready",
        },
    )

    result = hc.get_job_queue_status(HOST, port=PORT)

    assert route.called

    assert result == {
        "queued_jobs": [{"filename": "a.gcode"}, {"filename": "b.gcode"}],
        "queue_state": "ready",
    }

@respx.mock
def test_enqueue_job_sends_filenames_and_reset_as_json_body():
    route = respx.post(f"{BASE_URL}/server/job_queue/job").respond(
        200, json={"queued_jobs": [{"filename": "plate.gcode"}], "queue_state": "ready"}
    )

    result = hc.enqueue_job(HOST, ["plate.gcode"], port=PORT)

    assert route.called
    sent_request = route.calls.last.request
    body = json.loads(sent_request.content)
    assert body == {"filenames": ["plate.gcode"], "reset": False}
    assert result == {
        "queued_jobs": [{"filename": "plate.gcode"}],
        "queue_state": "ready",
    }

@respx.mock
def test_enqueue_job_passes_reset_true_explicitly():
    route = respx.post(f"{BASE_URL}/server/job_queue/job").respond(
        200, json={"queued_jobs": [], "queue_state": "ready"}
    )

    hc.enqueue_job(HOST, ["a.gcode", "b.gcode"], port=PORT, reset=True)

    sent_request = route.calls.last.request
    body = json.loads(sent_request.content)
    assert body == {"filenames": ["a.gcode", "b.gcode"], "reset": True}

@respx.mock
def test_start_uploaded_print_sends_filename_as_query_param():
    route = respx.post(f"{BASE_URL}/printer/print/start").respond(
        200, json="ok"
    )

    result = hc.start_uploaded_print(HOST, "plate.gcode", port=PORT)

    assert route.called
    sent_request = route.calls.last.request
    assert sent_request.url.params.get("filename") == "plate.gcode"

    assert sent_request.content == b""

    assert result == "ok"
    assert isinstance(result, str)

@respx.mock
def test_start_uploaded_print_raises_on_404_file_not_found():
    respx.post(f"{BASE_URL}/printer/print/start").respond(
        404, json={"error": {"message": "File không tồn tại"}}
    )

    with pytest.raises(hc.MoonrakerClientError):
        hc.start_uploaded_print(HOST, "khong_ton_tai.gcode", port=PORT)

@respx.mock
def test_get_spoolman_spool_sends_correct_proxy_body():
    route = respx.post(f"{BASE_URL}/server/spoolman/proxy").respond(
        200,
        json={
            "response": {
                "filament": {"material": "PLA", "name": "Red"},
                "used_weight": 123.4,
                "remaining_weight": 876.6,
            },
            "error": None,
        },
    )

    result = hc.get_spoolman_spool(HOST, "42", port=PORT)

    assert route.called
    sent_request = route.calls.last.request
    body = json.loads(sent_request.content)
    assert body == {
        "use_v2_response": True,
        "request_method": "GET",
        "path": "/v1/spool/42",
    }

    assert result == {
        "response": {
            "filament": {"material": "PLA", "name": "Red"},
            "used_weight": 123.4,
            "remaining_weight": 876.6,
        },
        "error": None,
    }

@respx.mock
def test_get_spoolman_spool_returns_error_envelope_without_raising():
    respx.post(f"{BASE_URL}/server/spoolman/proxy").respond(
        200,
        json={
            "response": None,
            "error": {"status_code": 404, "message": "Spool not found"},
        },
    )

    result = hc.get_spoolman_spool(HOST, "khong_ton_tai", port=PORT)

    assert result == {
        "response": None,
        "error": {"status_code": 404, "message": "Spool not found"},
    }

@respx.mock
def test_get_webcams_returns_webcams_array_unwrapped():
    route = respx.get(f"{BASE_URL}/server/webcams/list").respond(
        200,
        json={
            "webcams": [
                {
                    "name": "testcam",
                    "location": "printer",
                    "service": "mjpegstreamer",
                    "enabled": True,
                    "stream_url": "/webcam/?action=stream",
                    "snapshot_url": "/webcam/?action=snapshot",
                    "source": "config",
                    "uid": "55d3801e-fdc1-438d-8728-2fff8b83b909",
                }
            ]
        },
    )

    result = hc.get_webcams(HOST, port=PORT)

    assert route.called

    assert result == [
        {
            "name": "testcam",
            "location": "printer",
            "service": "mjpegstreamer",
            "enabled": True,
            "stream_url": "/webcam/?action=stream",
            "snapshot_url": "/webcam/?action=snapshot",
            "source": "config",
            "uid": "55d3801e-fdc1-438d-8728-2fff8b83b909",
        }
    ]

@respx.mock
def test_get_webcams_returns_empty_list_when_none_configured():
    respx.get(f"{BASE_URL}/server/webcams/list").respond(200, json={"webcams": []})

    result = hc.get_webcams(HOST, port=PORT)

    assert result == []

@respx.mock
def test_get_webcams_raises_on_http_error_status():
    respx.get(f"{BASE_URL}/server/webcams/list").respond(500, text="internal error")

    with pytest.raises(hc.MoonrakerClientError):
        hc.get_webcams(HOST, port=PORT)

@respx.mock
def test_get_webcams_raises_on_network_error():
    respx.get(f"{BASE_URL}/server/webcams/list").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with pytest.raises(hc.MoonrakerClientError):
        hc.get_webcams(HOST, port=PORT)

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
