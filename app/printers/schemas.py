
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT

class PrinterCreateRequest(BaseModel):

    name: str
    ip: str
    moonraker_port: int = Field(default=DEFAULT_MOONRAKER_PORT)
    model: Optional[str] = None
    api_key: Optional[str] = None

class PrinterResponse(BaseModel):

    id: int
    name: str
    ip: str
    moonraker_port: int
    model: Optional[str] = None
    api_key: Optional[str] = None
    moonraker_version: Optional[str] = None
    klipper_version: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    status: str
    is_held: bool
    created_at: str
    updated_at: str
