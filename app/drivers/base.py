
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.moonraker import http_client
from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT, PrinterStatus

class PrinterDriver(ABC):

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_MOONRAKER_PORT,
        api_key: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key

    @abstractmethod
    def get_server_info(self) -> dict:
        pass

    @abstractmethod
    def get_printer_info(self) -> dict:
        pass

    @abstractmethod
    def get_status(self) -> PrinterStatus:
        pass

    @abstractmethod
    def gcode_script(self, script: str) -> dict:
        pass

    @abstractmethod
    def upload_and_print(self, filename: str, file_content: bytes) -> dict:
        pass

    @abstractmethod
    def cancel_job(self) -> dict:
        pass

    @abstractmethod
    def pause_job(self) -> dict:
        pass

    @abstractmethod
    def resume_job(self) -> dict:
        pass

    @abstractmethod
    def check_if_printing(self) -> bool:
        pass

    @abstractmethod
    def emergency_stop(self) -> dict:
        pass

class BaseKlipperDriver(PrinterDriver):

    def get_server_info(self) -> dict:
        return http_client.get_server_info(
            self.host, port=self.port, api_key=self.api_key
        )

    def get_printer_info(self) -> dict:
        return http_client.get_printer_info(
            self.host, port=self.port, api_key=self.api_key
        )

    def get_status(self) -> PrinterStatus:
        return http_client.get_status(self.host, port=self.port, api_key=self.api_key)

    def gcode_script(self, script: str) -> dict:
        return http_client.gcode_script(
            self.host, script, port=self.port, api_key=self.api_key
        )

    def upload_and_print(self, filename: str, file_content: bytes) -> dict:
        return http_client.upload_and_print(
            self.host,
            filename,
            file_content,
            port=self.port,
            api_key=self.api_key,
        )

    def cancel_job(self) -> dict:
        return http_client.cancel_job(self.host, port=self.port, api_key=self.api_key)

    def pause_job(self) -> dict:
        return http_client.pause_job(self.host, port=self.port, api_key=self.api_key)

    def resume_job(self) -> dict:
        return http_client.resume_job(self.host, port=self.port, api_key=self.api_key)

    def check_if_printing(self) -> bool:
        return http_client.check_if_printing(
            self.host, port=self.port, api_key=self.api_key
        )

    def emergency_stop(self) -> dict:
        return http_client.emergency_stop(
            self.host, port=self.port, api_key=self.api_key
        )
