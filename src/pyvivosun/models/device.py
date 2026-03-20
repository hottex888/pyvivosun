"""Device models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DeviceType(StrEnum):
    """Known Vivosun device types."""

    CONTROLLER = "controller"
    LIGHT = "light"
    CIRCULATION_FAN = "circulation_fan"
    DUCT_FAN = "duct_fan"
    HUMIDIFIER = "humidifier"
    HEATER = "heater"
    CAMERA = "camera"
    UNKNOWN = "unknown"


@dataclass
class Device:
    """A Vivosun device."""

    device_id: str
    client_id: str
    name: str
    device_type: DeviceType
    topic_prefix: str
    scene_id: str
    online: bool
    hw_id: str | None = None
    model: str | None = None
    camera_username: str | None = None
    camera_password: str | None = None
