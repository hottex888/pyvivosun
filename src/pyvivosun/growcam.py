"""Higher-level local GrowCam wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiohttp

from .camera import (
    fetch_camera_diagnostics,
    fetch_camera_encode_info,
    fetch_camera_network_info,
    fetch_camera_overlay_settings,
    fetch_camera_snapshot,
    fetch_camera_status_led,
    fetch_camera_storage_info,
    fetch_camera_time_settings,
    fetch_camera_timelapse_config,
    list_camera_recordings,
    sync_camera_clock,
    update_camera_overlay_settings,
    update_camera_status_led,
    update_camera_time_settings,
)
from .models.camera import (
    CameraDiagnostics,
    CameraEncodeInfo,
    CameraNetworkInfo,
    CameraOverlaySettings,
    CameraRecording,
    CameraStorageInfo,
    CameraTimelapseConfig,
    CameraTimeSettings,
)
from .rps import DEFAULT_RPS_AUTH_CODES, discover_camera_ip_via_rps


@dataclass
class GrowCamClient:
    """Convenience wrapper around local GrowCam operations."""

    camera_ip: str
    username: str
    password: str

    def network_info(self) -> CameraNetworkInfo:
        return fetch_camera_network_info(self.camera_ip, self.username, self.password)

    def diagnostics(self) -> CameraDiagnostics:
        return fetch_camera_diagnostics(self.camera_ip, self.username, self.password)

    def encode_info(self) -> CameraEncodeInfo:
        return fetch_camera_encode_info(self.camera_ip, self.username, self.password)

    def storage_info(self) -> CameraStorageInfo:
        return fetch_camera_storage_info(self.camera_ip, self.username, self.password)

    def timelapse_config(self) -> CameraTimelapseConfig | None:
        return fetch_camera_timelapse_config(
            self.camera_ip, self.username, self.password
        )

    def snapshot(self) -> bytes:
        return fetch_camera_snapshot(self.camera_ip, self.username, self.password)

    def time_settings(self) -> CameraTimeSettings:
        return fetch_camera_time_settings(self.camera_ip, self.username, self.password)

    def overlay_settings(self) -> CameraOverlaySettings:
        return fetch_camera_overlay_settings(
            self.camera_ip,
            self.username,
            self.password,
        )

    def status_led_enabled(self) -> bool | None:
        return fetch_camera_status_led(self.camera_ip, self.username, self.password)

    def update_time_settings(
        self,
        *,
        time_format: str | None = None,
        date_format: str | None = None,
    ) -> CameraTimeSettings:
        return update_camera_time_settings(
            self.camera_ip,
            self.username,
            self.password,
            time_format=time_format,
            date_format=date_format,
        )

    def update_overlays(
        self,
        *,
        timestamp_enabled: bool | None = None,
        logo_enabled: bool | None = None,
    ) -> CameraOverlaySettings:
        return update_camera_overlay_settings(
            self.camera_ip,
            self.username,
            self.password,
            timestamp_enabled=timestamp_enabled,
            logo_enabled=logo_enabled,
        )

    def set_status_led(self, enabled: bool) -> bool | None:
        return update_camera_status_led(
            self.camera_ip,
            self.username,
            self.password,
            enabled,
        )

    def sync_clock(
        self,
        *,
        when: datetime | None = None,
        timezone_offset_minutes: int | None = None,
    ) -> bool:
        return sync_camera_clock(
            self.camera_ip,
            self.username,
            self.password,
            when=when,
            timezone_offset_minutes=timezone_offset_minutes,
        )

    def recordings(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        event: str = "*",
    ) -> list[CameraRecording]:
        return list_camera_recordings(
            self.camera_ip,
            self.username,
            self.password,
            start_time=start_time,
            end_time=end_time,
            event=event,
        )

    @classmethod
    async def discover_ip(
        cls,
        session: aiohttp.ClientSession,
        *,
        serial_number: str,
        username: str,
        password: str,
        auth_codes: tuple[str, ...] = DEFAULT_RPS_AUTH_CODES,
    ) -> str | None:
        return await discover_camera_ip_via_rps(
            session,
            serial_number=serial_number,
            username=username,
            password=password,
            auth_codes=auth_codes,
        )

    @classmethod
    async def discover(
        cls,
        session: aiohttp.ClientSession,
        *,
        serial_number: str,
        username: str,
        password: str,
        auth_codes: tuple[str, ...] = DEFAULT_RPS_AUTH_CODES,
    ) -> GrowCamClient | None:
        camera_ip = await cls.discover_ip(
            session,
            serial_number=serial_number,
            username=username,
            password=password,
            auth_codes=auth_codes,
        )
        if camera_ip is None:
            return None
        return cls(camera_ip=camera_ip, username=username, password=password)
