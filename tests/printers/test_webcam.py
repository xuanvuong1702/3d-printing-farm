
from __future__ import annotations

import sqlite3

from app.db.migrate import run_migrations
from app.printers.schemas import PrinterResponse
from app.printers.service import get_printer, resolve_webcam_stream_url

_INSERT_PRINTER_SQL = (
    "INSERT INTO printers (name, ip, moonraker_port, capabilities) "
    "VALUES (?, ?, ?, ?)"
)

def _insert_printer(db_path: str, *, ip: str = "192.168.1.50") -> int:
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            _INSERT_PRINTER_SQL, ("Test Printer", ip, 7125, "[]")
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()

def test_get_printer_found_returns_printer_response(tmp_path) -> None:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)
    printer_id = _insert_printer(db_path, ip="192.168.1.50")

    result = get_printer(printer_id, db_path=db_path)

    assert isinstance(result, PrinterResponse)
    assert result.id == printer_id
    assert result.ip == "192.168.1.50"
    assert result.moonraker_port == 7125

def test_get_printer_not_found_returns_none(tmp_path) -> None:
    db_path = str(tmp_path / "test_printers.db")
    run_migrations(db_path)

    result = get_printer(9999, db_path=db_path)

    assert result is None

def _make_printer(ip: str = "192.168.1.50") -> PrinterResponse:
    return PrinterResponse(
        id=1,
        name="Test Printer",
        ip=ip,
        moonraker_port=7125,
        capabilities=[],
        status="IDLE",
        is_held=False,
        created_at="2026-09-15T00:00:00Z",
        updated_at="2026-09-15T00:00:00Z",
    )

def test_resolve_webcam_stream_url_no_webcams_returns_none() -> None:
    printer = _make_printer()

    assert resolve_webcam_stream_url(printer, []) is None

def test_resolve_webcam_stream_url_none_enabled_returns_none() -> None:
    printer = _make_printer()
    webcams = [
        {"enabled": False, "stream_url": "/webcam/?action=stream"},
        {"enabled": False, "stream_url": "http://camera.lan/stream"},
    ]

    assert resolve_webcam_stream_url(printer, webcams) is None

def test_resolve_webcam_stream_url_absolute_url_used_verbatim() -> None:
    printer = _make_printer(ip="192.168.1.50")
    webcams = [{"enabled": True, "stream_url": "http://camera.lan/webcam?action=stream"}]

    result = resolve_webcam_stream_url(printer, webcams)

    assert result == "http://camera.lan/webcam?action=stream"

def test_resolve_webcam_stream_url_relative_path_resolved_with_port_80() -> None:
    printer = _make_printer(ip="192.168.1.50")
    webcams = [{"enabled": True, "stream_url": "/webcam/?action=stream"}]

    result = resolve_webcam_stream_url(printer, webcams)

    assert result == "http://192.168.1.50/webcam/?action=stream"

def test_resolve_webcam_stream_url_picks_first_enabled() -> None:
    printer = _make_printer(ip="192.168.1.50")
    webcams = [
        {"enabled": False, "stream_url": "/ignored/?action=stream"},
        {"enabled": True, "stream_url": "/first/?action=stream"},
        {"enabled": True, "stream_url": "/second/?action=stream"},
    ]

    result = resolve_webcam_stream_url(printer, webcams)

    assert result == "http://192.168.1.50/first/?action=stream"
