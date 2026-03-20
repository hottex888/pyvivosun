"""Tests for GrowCamClient wrapper."""

from __future__ import annotations

from datetime import datetime

import aiohttp
import pytest

from pyvivosun.growcam import GrowCamClient
from pyvivosun.models import (
    CameraDiagnostics,
    CameraEncodeInfo,
    CameraNetworkInfo,
    CameraOverlaySettings,
    CameraRecording,
    CameraTimeSettings,
)


def test_growcam_client_uses_local_helpers(monkeypatch) -> None:
    client = GrowCamClient(camera_ip="10.0.15.202", username="abjd", password="4kt5em")

    monkeypatch.setattr(
        "pyvivosun.growcam.fetch_camera_network_info",
        lambda ip, username, password: CameraNetworkInfo(wifi_ip=ip),
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.fetch_camera_diagnostics",
        lambda ip, username, password: CameraDiagnostics(
            device_model="K80XV40",
            status_led_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.fetch_camera_status_led",
        lambda ip, username, password: False,
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.fetch_camera_encode_info",
        lambda ip, username, password: CameraEncodeInfo(),
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.fetch_camera_snapshot",
        lambda ip, username, password: b"jpeg",
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.fetch_camera_time_settings",
        lambda ip, username, password: CameraTimeSettings(
            timezone_offset_minutes=60,
        ),
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.fetch_camera_overlay_settings",
        lambda ip, username, password: CameraOverlaySettings(
            timestamp_enabled=True,
            logo_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.update_camera_overlay_settings",
        lambda ip, username, password, timestamp_enabled=None, logo_enabled=None:
        CameraOverlaySettings(
            timestamp_enabled=(
                timestamp_enabled if timestamp_enabled is not None else True
            ),
            logo_enabled=logo_enabled if logo_enabled is not None else False,
        ),
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.update_camera_status_led",
        lambda ip, username, password, enabled: enabled,
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.sync_camera_clock",
        lambda ip, username, password, when=None, timezone_offset_minutes=None: True,
    )
    monkeypatch.setattr(
        "pyvivosun.growcam.list_camera_recordings",
        lambda ip, username, password, start_time, end_time, event='*': [
            CameraRecording(
                start_time=start_time,
                end_time=end_time,
                file_name="/idea0/test.h264",
                length_bytes=10,
                disk_no=0,
                category="regular",
            )
        ],
    )

    assert client.network_info().wifi_ip == "10.0.15.202"
    assert client.diagnostics().device_model == "K80XV40"
    assert client.status_led_enabled() is False
    assert client.encode_info() is not None
    assert client.time_settings().timezone_offset_minutes == 60
    assert client.overlay_settings().timestamp_enabled is True
    assert client.update_overlays(timestamp_enabled=False).timestamp_enabled is False
    assert client.set_status_led(True) is True
    assert client.sync_clock() is True
    assert client.snapshot() == b"jpeg"
    recordings = client.recordings(
        start_time=datetime(2026, 1, 1),
        end_time=datetime(2026, 1, 2),
    )
    assert len(recordings) == 1


@pytest.mark.asyncio
async def test_growcam_client_discover(monkeypatch) -> None:
    async def _fake_discover(
        session,
        *,
        serial_number: str,
        username: str,
        password: str,
        auth_codes: tuple[str, ...],
    ) -> str | None:
        _ = session, serial_number, username, password, auth_codes
        return "10.0.15.202"

    monkeypatch.setattr("pyvivosun.growcam.discover_camera_ip_via_rps", _fake_discover)

    async with aiohttp.ClientSession() as session:
        client = await GrowCamClient.discover(
            session,
            serial_number="5a8ddedd3c1e7674",
            username="abjd",
            password="4kt5em",
        )

    assert client is not None
    assert client.camera_ip == "10.0.15.202"
