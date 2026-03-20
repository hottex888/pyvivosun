"""Models for experimental camera RPS discovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RpsStatus:
    """Single camera status entry returned by the RPS status service."""

    serial_number: str
    status: str
    device_type: str | None = None
    server_ip: str | None = None
    server_port: int | None = None
    device_port: int | None = None
    wan_ip: str | None = None
    kcp_enabled: bool | None = None
