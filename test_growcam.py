"""Dedicated GrowCam diagnostic and control script."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from time import sleep

from pyvivosun.auth import AuthManager
from pyvivosun.growcam import GrowCamClient
from pyvivosun.rest import RestClient

CREDENTIALS_FILE = Path(__file__).parent / "credentials.env"
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def load_credentials() -> tuple[str, str]:
    """Load credentials from credentials.env, fall back to interactive prompt."""
    email = os.environ.get("VIVOSUN_EMAIL", "")
    password = os.environ.get("VIVOSUN_PASSWORD", "")

    if not email and CREDENTIALS_FILE.exists():
        for line in CREDENTIALS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "VIVOSUN_EMAIL":
                email = value.strip()
            elif key.strip() == "VIVOSUN_PASSWORD":
                password = value.strip()

    if not email or not password:
        import getpass

        email = email or input("Email: ")
        password = password or getpass.getpass("Password: ")

    return email, password


def _camera_credentials(device: dict[str, object]) -> tuple[str | None, str | None]:
    """Extract camera LAN credentials from the raw device payload."""
    setting = device.get("setting")
    if not isinstance(setting, dict):
        return None, None
    jf = setting.get("jf")
    if not isinstance(jf, dict):
        return None, None
    username = jf.get("devUser")
    password = jf.get("devPass")
    return (
        username if isinstance(username, str) and username else None,
        password if isinstance(password, str) and password else None,
    )


def _is_camera(device: dict[str, object]) -> bool:
    name = str(device.get("name", "")).lower()
    return "growcam" in name or "camera" in name


def _find_camera_device(devices: list[dict[str, object]]) -> dict[str, object] | None:
    for device in devices:
        if _is_camera(device):
            return device
    return None


def _overlay_env(name: str) -> bool | None:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "on", "yes"}:
        return True
    if raw in {"0", "false", "off", "no"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw}")


def _choice_env(name: str, allowed: set[str]) -> str | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    if raw not in allowed:
        raise ValueError(
            f"Invalid value for {name}: {raw!r}; expected one of {sorted(allowed)}"
        )
    return raw


def _print_diagnostics(camera: GrowCamClient) -> None:
    network = camera.network_info()
    encode = camera.encode_info()
    diagnostics = camera.diagnostics()
    timelapse = camera.timelapse_config()

    print("Camera diagnostics:")
    print(
        " - Identity: "
        f"model={diagnostics.device_model} hardware={diagnostics.hardware} "
        f"hardware_version={diagnostics.hardware_version}"
    )
    print(
        " - Firmware: "
        f"firmware={diagnostics.firmware_version} serial={diagnostics.serial_number}"
    )
    print(
        " - Network: "
        f"wifi_ip={network.wifi_ip} common_ip={network.common_ip} "
        f"tcp_port={network.tcp_port} ssid={network.ssid} "
        f"signal={diagnostics.signal_strength}"
    )
    print(
        " - MAC: "
        f"mac={diagnostics.mac_address} wifi_mac={diagnostics.wifi_mac_address}"
    )
    if encode.main:
        print(
            " - Main encode: "
            f"{encode.main.codec} {encode.main.resolution} "
            f"{encode.main.bitrate_kbps}kbps {encode.main.fps}fps"
        )
    if encode.extra:
        print(
            " - Extra encode: "
            f"{encode.extra.codec} {encode.extra.resolution} "
            f"{encode.extra.bitrate_kbps}kbps {encode.extra.fps}fps"
        )
    print(
        " - Image: "
        f"mirror={diagnostics.picture_mirror} flip={diagnostics.picture_flip} "
        f"night_mode={diagnostics.night_mode}"
    )
    print(
        " - Time: "
        f"offset={diagnostics.timezone_offset_minutes} "
        f"current={diagnostics.current_time} "
        f"date_format={diagnostics.date_format} time_format={diagnostics.time_format}"
    )
    print(
        " - Overlays: "
        f"timestamp={diagnostics.timestamp_enabled} logo={diagnostics.logo_enabled} "
        f"status_led={diagnostics.status_led_enabled}"
    )
    print(
        " - Storage: "
        f"used={diagnostics.sd_used_mb}MB total={diagnostics.sd_total_mb}MB "
        f"free={diagnostics.sd_free_mb}MB"
    )
    if timelapse is None:
        print(" - Timelapse: unavailable")
    else:
        print(
            " - Timelapse: "
            f"enabled={timelapse.enabled} interval={timelapse.interval_seconds}s"
        )


async def main() -> None:
    email, password = load_credentials()

    rest = RestClient()
    auth = AuthManager(rest, email, password)

    try:
        print("--- Logging in...")
        await auth.ensure_authenticated()

        headers = auth.get_rest_headers()
        raw_devices = await rest.get_device_list(headers)
        camera_device = _find_camera_device(raw_devices)
        if camera_device is None:
            print("No GrowCam device found on this account")
            return

        print("--- Raw camera payload...")
        print(json.dumps(camera_device, indent=2, default=str))

        serial_number = str(camera_device.get("hwId", ""))
        username, camera_password = _camera_credentials(camera_device)
        if not serial_number or not username or not camera_password:
            print("Camera missing hwId or LAN credentials in setting.jf")
            return

        session = await rest._ensure_session()
        discovered_ip = await GrowCamClient.discover_ip(
            session,
            serial_number=serial_number,
            username=username,
            password=camera_password,
        )
        print(f"--- RPS discovered IP: {discovered_ip}")
        if not discovered_ip:
            return

        camera = GrowCamClient(
            camera_ip=discovered_ip,
            username=username,
            password=camera_password,
        )

        _print_diagnostics(camera)

        requested_timestamp = _overlay_env("VIVOSUN_CAMERA_TIMESTAMP")
        requested_logo = _overlay_env("VIVOSUN_CAMERA_LOGO")
        requested_status_led = _overlay_env("VIVOSUN_CAMERA_STATUS_LED")
        requested_time_format = _choice_env("VIVOSUN_CAMERA_TIME_FORMAT", {"12", "24"})
        requested_date_format = _choice_env(
            "VIVOSUN_CAMERA_DATE_FORMAT",
            {"YYMMDD", "MMDDYY", "DDMMYY"},
        )

        if requested_time_format is not None or requested_date_format is not None:
            time_settings = camera.update_time_settings(
                time_format=requested_time_format,
                date_format=requested_date_format,
            )
            print(
                "Updated time settings: "
                f"date_format={time_settings.date_format} "
                f"time_format={time_settings.time_format}"
            )

        if requested_timestamp is not None or requested_logo is not None:
            overlays = camera.update_overlays(
                timestamp_enabled=requested_timestamp,
                logo_enabled=requested_logo,
            )
            print(
                "Updated overlays: "
                f"timestamp={overlays.timestamp_enabled} logo={overlays.logo_enabled}"
            )

        if requested_status_led is not None:
            status_led = camera.set_status_led(requested_status_led)
            print(f"Updated status LED: {status_led}")

        changed = camera.sync_clock()
        print(f"Clock sync changed settings: {changed}")

        if (
            requested_time_format is not None
            or requested_date_format is not None
            or requested_timestamp is not None
            or requested_logo is not None
            or requested_status_led is not None
            or changed
        ):
            print(
                "Waiting 5 seconds for camera settings to propagate before snapshot..."
            )
            sleep(5)

        snapshot = camera.snapshot()
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = SNAPSHOT_DIR / f"{serial_number}_{timestamp}.jpg"
        snapshot_path.write_bytes(snapshot)
        print(f"Snapshot saved: {snapshot_path} ({len(snapshot)} bytes)")

        recordings = camera.recordings(
            start_time=datetime(2025, 3, 17),
            end_time=datetime(2026, 3, 17, 23, 59, 59),
            event="*",
        )
        print(f"Recordings found: {len(recordings)}")
        for recording in recordings[:5]:
            print(
                " - "
                f"{recording.category} {recording.start_time} -> {recording.end_time} "
                f"{recording.file_name}"
            )
    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
