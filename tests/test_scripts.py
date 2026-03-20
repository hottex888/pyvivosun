"""Tests for root diagnostic scripts."""

from __future__ import annotations

import test_growcam
import test_live


def test_live_sorts_camera_last() -> None:
    devices = [
        {"name": "GrowCam C4"},
        {"name": "GrowHub E42A"},
        {"name": "AeroStream H19"},
    ]

    sorted_devices = test_live._sorted_devices_for_display(devices)

    assert [device["name"] for device in sorted_devices] == [
        "GrowHub E42A",
        "AeroStream H19",
        "GrowCam C4",
    ]


def test_growcam_finds_first_camera_device() -> None:
    devices = [
        {"name": "GrowHub E42A"},
        {"name": "GrowCam C4", "deviceId": "camera-1"},
        {"name": "AeroFlux W70"},
    ]

    camera = test_growcam._find_camera_device(devices)

    assert camera is not None
    assert camera["deviceId"] == "camera-1"
