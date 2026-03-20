"""Data models for pyvivosun."""

from .auth import AwsCredentials, TokenSet
from .camera import (
    CameraDiagnostics,
    CameraEncodeInfo,
    CameraEncodeProfile,
    CameraNetworkInfo,
    CameraOverlaySettings,
    CameraRecording,
    CameraStorageInfo,
    CameraStoragePartition,
    CameraTimelapseConfig,
    CameraTimeSettings,
)
from .device import Device, DeviceType
from .event import EventType, VivosunEvent
from .rps import RpsStatus
from .state import (
    CirculationFanState,
    DeviceState,
    DuctFanState,
    HeaterState,
    HumidifierState,
    LightState,
    SensorData,
    parse_shadow_to_state,
)

__all__ = [
    "AwsCredentials",
    "CameraEncodeInfo",
    "CameraEncodeProfile",
    "CameraDiagnostics",
    "CameraNetworkInfo",
    "CameraRecording",
    "CameraStorageInfo",
    "CameraStoragePartition",
    "CameraTimeSettings",
    "CameraTimelapseConfig",
    "CameraOverlaySettings",
    "CirculationFanState",
    "Device",
    "DeviceState",
    "DeviceType",
    "DuctFanState",
    "EventType",
    "HeaterState",
    "HumidifierState",
    "LightState",
    "RpsStatus",
    "SensorData",
    "TokenSet",
    "VivosunEvent",
    "parse_shadow_to_state",
]
