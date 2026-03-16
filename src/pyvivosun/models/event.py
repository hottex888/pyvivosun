"""Event models for state change notifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """Types of events emitted by VivosunClient."""

    STATE_CHANGED = "state_changed"
    DEVICE_ONLINE = "device_online"
    DEVICE_OFFLINE = "device_offline"
    CONNECTION_CHANGED = "connection_changed"


@dataclass
class VivosunEvent:
    """An event from the Vivosun system."""

    event_type: EventType
    device_id: str | None = None
    data: Any = None
