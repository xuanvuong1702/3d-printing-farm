
from __future__ import annotations

import app.main as main_module
from app.moonraker.http_client import MoonrakerClientError

def test_printer_detail_camera_tab_fallback_when_no_webcams(
    client, insert_printer
) -> None:
    printer_id = insert_printer(name="May Khong Camera", status="IDLE")

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    assert "Máy này chưa cấu hình camera." in body
    assert "camera-frame__img" not in body

def test_printer_detail_camera_tab_fallback_when_all_webcams_disabled(
    client, insert_printer, monkeypatch
) -> None:
    printer_id = insert_printer(name="May Camera Tat", status="IDLE")

    def _get_webcams_all_disabled(host, port=7125, api_key=None):
        return [
            {"enabled": False, "stream_url": "/webcam/?action=stream"},
            {"enabled": False, "stream_url": "http://other-cam/stream"},
        ]

    monkeypatch.setattr(main_module, "get_webcams", _get_webcams_all_disabled)

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    assert "Máy này chưa cấu hình camera." in body
    assert "camera-frame__img" not in body

def test_printer_detail_camera_tab_falls_back_when_moonraker_unreachable(
    client, insert_printer, monkeypatch
) -> None:
    printer_id = insert_printer(name="May Moonraker Loi", status="IDLE")

    def _get_webcams_raises(host, port=7125, api_key=None):
        raise MoonrakerClientError("Timeout khi gọi Moonraker")

    monkeypatch.setattr(main_module, "get_webcams", _get_webcams_raises)

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    assert "Máy này chưa cấu hình camera." in body
    assert "camera-frame__img" not in body

def test_printer_detail_camera_tab_renders_absolute_stream_url(
    client, insert_printer, monkeypatch
) -> None:
    printer_id = insert_printer(name="May Camera Tuyet Doi", status="IDLE")

    def _get_webcams_absolute(host, port=7125, api_key=None):
        return [
            {"enabled": True, "stream_url": "http://192.168.1.50:8080/stream"}
        ]

    monkeypatch.setattr(main_module, "get_webcams", _get_webcams_absolute)

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    assert '<img src="http://192.168.1.50:8080/stream"' in body
    assert "Máy này chưa cấu hình camera." not in body

def test_printer_detail_camera_tab_renders_relative_stream_url_with_port_80(
    client, insert_printer, monkeypatch
) -> None:
    printer_id = insert_printer(
        name="May Camera Tuong Doi",
        status="IDLE",
        ip="10.0.0.42",
        moonraker_port=7125,
    )

    def _get_webcams_relative(host, port=7125, api_key=None):
        return [{"enabled": True, "stream_url": "/webcam/?action=stream"}]

    monkeypatch.setattr(main_module, "get_webcams", _get_webcams_relative)

    response = client.get(f"/printers/{printer_id}/detail")
    body = response.text

    assert response.status_code == 200
    assert '<img src="http://10.0.0.42/webcam/?action=stream"' in body
    assert "10.0.0.42:7125" not in body
