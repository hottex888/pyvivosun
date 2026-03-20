"""Live test script — login, inspect devices, and probe GrowCam access."""

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
    """Return whether a raw device payload appears to be a GrowCam."""
    name = str(device.get("name", "")).lower()
    return "growcam" in name or "camera" in name


def _sorted_devices_for_display(
    devices: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Sort devices so cameras are always printed last."""
    return sorted(devices, key=lambda device: (1 if _is_camera(device) else 0,))


def _overlay_env(name: str) -> bool | None:
    """Parse optional camera overlay env vars."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "on", "yes"}:
        return True
    if raw in {"0", "false", "off", "no"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw}")


def _choice_env(name: str, allowed: set[str]) -> str | None:
    """Parse optional enum-like env vars."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    if raw not in allowed:
        raise ValueError(
            f"Invalid value for {name}: {raw!r}; expected one of {sorted(allowed)}"
        )
    return raw


async def _probe_camera(rest: RestClient, device: dict[str, object]) -> None:
    """Run the experimental GrowCam discovery and local diagnostics."""
    name = str(device.get("name", "Unknown"))
    serial_number = str(device.get("hwId", ""))
    device_id = str(device.get("deviceId", ""))
    username, password = _camera_credentials(device)

    print(f"\n--- GrowCam diagnostics for '{name}' ({device_id})...")
    if not serial_number:
        print("  Missing hwId/serial, cannot run RPS discovery")
        return
    if not username or not password:
        print("  Missing camera LAN credentials in setting.jf")
        return

    session = await rest._ensure_session()
    discovered_ip = await GrowCamClient.discover_ip(
        session,
        serial_number=serial_number,
        username=username,
        password=password,
    )
    print(f"  RPS discovered IP: {discovered_ip}")
    if not discovered_ip:
        return

    camera = GrowCamClient(
        camera_ip=discovered_ip,
        username=username,
        password=password,
    )

    network = camera.network_info()
    print(f"  WiFi IP: {network.wifi_ip}")
    print(f"  Common IP: {network.common_ip}")
    print(f"  TCP port: {network.tcp_port}")
    print(f"  SSID: {network.ssid}")

    encode = camera.encode_info()
    if encode.main:
        print(
            "  Main encode: "
            f"{encode.main.codec} {encode.main.resolution} "
            f"{encode.main.bitrate_kbps}kbps {encode.main.fps}fps"
        )
    if encode.extra:
        print(
            "  Extra encode: "
            f"{encode.extra.codec} {encode.extra.resolution} "
            f"{encode.extra.bitrate_kbps}kbps {encode.extra.fps}fps"
        )

    diagnostics = camera.diagnostics()
    print("  Camera diagnostics:")
    print(
        "   - Identity: "
        f"model={diagnostics.device_model} "
        f"hardware={diagnostics.hardware} "
        f"hardware_version={diagnostics.hardware_version}"
    )
    print(
        "   - Firmware: "
        f"firmware={diagnostics.firmware_version} serial={diagnostics.serial_number}"
    )
    print(
        "   - Network: "
        f"wifi_ip={diagnostics.wifi_ip} common_ip={diagnostics.common_ip} "
        f"ssid={diagnostics.wlan_ssid} signal={diagnostics.signal_strength}"
    )
    print(
        "   - MAC: "
        f"mac={diagnostics.mac_address} wifi_mac={diagnostics.wifi_mac_address}"
    )
    print(
        "   - Image: "
        f"mirror={diagnostics.picture_mirror} flip={diagnostics.picture_flip} "
        "night_mode="
        f"{diagnostics.night_mode} white_light={diagnostics.white_light_mode}"
    )
    print(
        "   - Time: "
        f"offset={diagnostics.timezone_offset_minutes} "
        f"current={diagnostics.current_time} "
        f"date_format={diagnostics.date_format} time_format={diagnostics.time_format}"
    )
    print(
        "   - Overlays: "
        f"timestamp={diagnostics.timestamp_enabled} "
        f"logo={diagnostics.logo_enabled} status_led={diagnostics.status_led_enabled}"
    )
    print(
        "   - Storage: "
        f"used={diagnostics.sd_used_mb}MB total={diagnostics.sd_total_mb}MB "
        f"free={diagnostics.sd_free_mb}MB"
    )
    print("   - Status LED source: FbExtraStateCtrl.ison")

    storage = camera.storage_info()
    print(f"  Storage partitions: {len(storage.partitions)}")
    for partition in storage.partitions[:2]:
        print(
            "   - "
            f"driver={partition.driver_type} current={partition.is_current} "
            f"start={partition.start_time} end={partition.end_time}"
        )

    timelapse = camera.timelapse_config()
    if timelapse is None:
        print("  Timelapse config: unavailable")
    else:
        print(
            "  Timelapse config: "
            f"enabled={timelapse.enabled} interval={timelapse.interval_seconds}s"
        )

    time_settings = camera.time_settings()
    print(
        "  Camera time: "
        f"offset={time_settings.timezone_offset_minutes} "
        f"device_time={time_settings.device_time} "
        f"date_format={time_settings.date_format} "
        f"time_format={time_settings.time_format}"
    )

    overlays = camera.overlay_settings()
    print(
        "  Overlays: "
        f"timestamp={overlays.timestamp_enabled} "
        f"logo={overlays.logo_enabled}"
    )

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
            "  Updated time settings: "
            f"date_format={time_settings.date_format} "
            f"time_format={time_settings.time_format}"
        )
    if requested_timestamp is not None or requested_logo is not None:
        overlays = camera.update_overlays(
            timestamp_enabled=requested_timestamp,
            logo_enabled=requested_logo,
        )
        print(
            "  Updated overlays: "
            f"timestamp={overlays.timestamp_enabled} "
            f"logo={overlays.logo_enabled}"
        )

    if requested_status_led is not None:
        status_led = camera.set_status_led(requested_status_led)
        print(f"  Updated status LED: {status_led}")

    changed = camera.sync_clock()
    print(f"  Clock sync changed settings: {changed}")

    if (
        requested_time_format is not None
        or requested_date_format is not None
        or requested_timestamp is not None
        or requested_logo is not None
        or requested_status_led is not None
        or changed
    ):
        print("  Waiting 5 seconds for camera settings to propagate before snapshot...")
        sleep(5)

    snapshot = camera.snapshot()
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = SNAPSHOT_DIR / f"{serial_number}_{timestamp}.jpg"
    snapshot_path.write_bytes(snapshot)
    print(f"  Snapshot saved: {snapshot_path} ({len(snapshot)} bytes)")

    recordings = camera.recordings(
        start_time=datetime(2025, 3, 17),
        end_time=datetime(2026, 3, 17, 23, 59, 59),
        event="*",
    )
    print(f"  Recordings found: {len(recordings)}")
    for recording in recordings[:5]:
        print(
            "   - "
            f"{recording.category} {recording.start_time} -> {recording.end_time} "
            f"{recording.file_name}"
        )


async def main() -> None:
    email, password = load_credentials()

    rest = RestClient()
    auth = AuthManager(rest, email, password)

    try:
        print("\n--- Logging in...")
        await auth.ensure_authenticated()
        assert auth.tokens is not None
        print(f"OK — user_id: {auth.tokens.user_id}")

        print("\n--- Fetching device list...")
        headers = auth.get_rest_headers()
        raw_devices = await rest.get_device_list(headers)
        print(f"Found {len(raw_devices)} device(s):\n")

        for i, dev in enumerate(_sorted_devices_for_display(raw_devices), 1):
            print(f"=== Device {i} ===")
            print(json.dumps(dev, indent=2, default=str))
            print()

            if _is_camera(dev):
                try:
                    await _probe_camera(rest, dev)
                except Exception as e:
                    print(f"  Camera probe error: {e}")

        print("\n--- Fetching AWS IoT identity...")
        identity_data = await rest.get_aws_identity(headers)
        print(f"Host: {identity_data.get('awsHost')}")
        print(f"Region: {identity_data.get('awsRegion')}")

        # Try fetching point log for each device
        for dev in raw_devices:
            device_id = str(dev.get("deviceId", ""))
            scene = dev.get("scene", {})
            scene_id = scene.get("sceneId") if isinstance(scene, dict) else None
            name = dev.get("name", "Unknown")

            if not scene_id:
                print(f"\n--- Skipping point log for '{name}' (no sceneId)")
                continue

            print(f"\n--- Point log for '{name}' ({device_id})...")
            try:
                points = await rest.get_point_log(
                    headers, device_id, int(scene_id)
                )
                if points:
                    latest = points[-1]
                    print(f"  {len(points)} data point(s), latest entry keys:")
                    print(f"  {sorted(latest.keys())}")
                    print(f"  Raw latest: {json.dumps(latest, indent=4, default=str)}")
                else:
                    print("  (no data returned)")
            except Exception as e:
                print(f"  Error: {e}")

    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
