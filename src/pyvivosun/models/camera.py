"""GrowCam local camera models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CameraNetworkInfo:
    """Network-related camera information."""

    wifi_ip: str | None = None
    common_ip: str | None = None
    wifi_gateway: str | None = None
    common_gateway: str | None = None
    tcp_port: int | None = None
    udp_port: int | None = None
    http_port: int | None = None
    ssl_port: int | None = None
    ssid: str | None = None
    mac: str | None = None


@dataclass
class CameraEncodeProfile:
    """Single camera encode profile."""

    codec: str
    bitrate_kbps: int
    fps: int
    resolution: str
    gop: int


@dataclass
class CameraEncodeInfo:
    """Camera main/extra encode settings."""

    main: CameraEncodeProfile | None = None
    extra: CameraEncodeProfile | None = None


@dataclass
class CameraStoragePartition:
    """Single storage partition reported by the camera."""

    driver_type: int
    is_current: bool
    total_space: str
    remain_space: str
    start_time: str
    end_time: str


@dataclass
class CameraStorageInfo:
    """Storage state reported by the camera."""

    partitions: list[CameraStoragePartition] = field(default_factory=list)


@dataclass
class CameraTimelapseConfig:
    """Timelapse/epitome recording configuration."""

    enabled: bool
    interval_seconds: int
    start_time: str
    end_time: str
    time_sections: list[str] = field(default_factory=list)


@dataclass
class CameraRecording:
    """Single recording returned by OPFileQuery."""

    start_time: datetime
    end_time: datetime
    file_name: str
    length_bytes: int
    disk_no: int
    category: str


@dataclass
class CameraTimeSettings:
    """Camera time and timezone-related settings."""

    timezone_offset_minutes: int | None = None
    device_time: datetime | None = None
    date_format: str | None = None
    time_format: str | None = None
    dst_rule: str | None = None


@dataclass
class CameraOverlaySettings:
    """Camera overlay visibility settings."""

    timestamp_enabled: bool
    logo_enabled: bool


@dataclass
class CameraDiagnostics:
    """Aggregated camera diagnostics and useful user-facing info."""

    device_model: str | None = None
    hardware: str | None = None
    hardware_version: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    mac_address: str | None = None
    wifi_mac_address: str | None = None
    wifi_ip: str | None = None
    common_ip: str | None = None
    wlan_ssid: str | None = None
    signal_strength: int | None = None
    timezone_offset_minutes: int | None = None
    current_time: datetime | None = None
    date_format: str | None = None
    time_format: str | None = None
    sd_total_mb: int | None = None
    sd_used_mb: int | None = None
    sd_free_mb: int | None = None
    timestamp_enabled: bool | None = None
    logo_enabled: bool | None = None
    picture_mirror: bool | None = None
    picture_flip: bool | None = None
    night_mode: str | None = None
    white_light_mode: str | None = None
    status_led_enabled: bool | None = None
