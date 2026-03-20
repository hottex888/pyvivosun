"""Local GrowCam support over the XM/DVRIP protocol."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

from .exceptions import (
    AuthenticationError,
    ConnectionError,
    InvalidParameterError,
    VivosunError,
)
from .models.camera import (
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

_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_OP_FILE_QUERY_CODE = 1440


def _get_dvrip_cam_class() -> type[Any]:
    """Import and return the DVRIP camera class lazily."""
    try:
        from dvrip import DVRIPCam  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - exercised in real installs
        raise ConnectionError(
            "Local camera support requires the python-dvr package"
        ) from exc
    return cast(type[Any], DVRIPCam)


@contextmanager
def _camera_session(ip: str, username: str, password: str) -> Iterator[Any]:
    """Open and close a DVRIP camera session."""
    camera_class = _get_dvrip_cam_class()
    try:
        camera = camera_class(ip, user=username, password=password)
        if not camera.login():
            raise AuthenticationError("Camera login failed")
    except VivosunError:
        raise
    except Exception as exc:  # pragma: no cover - depends on third-party library
        raise ConnectionError(f"Failed to connect to camera at {ip}") from exc

    try:
        yield camera
    finally:
        camera.close()


def _decode_hex_ip(value: str | None) -> str | None:
    """Decode a little-endian hex IP like 0xCA0F000A."""
    if not value or not value.startswith("0x"):
        return None
    try:
        encoded = int(value, 16)
    except ValueError:
        return None
    octets = encoded.to_bytes(4, "little")
    return ".".join(str(part) for part in octets)


def _parse_encode_profile(data: Mapping[str, Any] | None) -> CameraEncodeProfile | None:
    """Parse a main or extra encode profile."""
    if not data:
        return None
    video = data.get("Video")
    if not isinstance(video, Mapping):
        return None
    return CameraEncodeProfile(
        codec=str(video.get("Compression", "")),
        bitrate_kbps=int(video.get("BitRate", 0)),
        fps=int(video.get("FPS", 0)),
        resolution=str(video.get("Resolution", "")),
        gop=int(video.get("GOP", 0)),
    )


def _parse_recording_category(file_name: str) -> str:
    """Infer the recording category from its filename markers."""
    if "[E]" in file_name:
        return "event"
    if "[R]" in file_name:
        return "regular"
    if "[H]" in file_name:
        return "high"
    return "unknown"


def fetch_camera_network_info(
    camera_ip: str, username: str, password: str
) -> CameraNetworkInfo:
    """Fetch network information from the local camera."""
    with _camera_session(camera_ip, username, password) as camera:
        wifi = camera.get_command("NetWork.Wifi", 1042)
        common = camera.get_command("NetWork.NetCommon", 1042)

    wifi_mapping = wifi if isinstance(wifi, Mapping) else {}
    common_mapping = common if isinstance(common, Mapping) else {}
    return CameraNetworkInfo(
        wifi_ip=_decode_hex_ip(_as_str(wifi_mapping.get("HostIP"))),
        common_ip=_decode_hex_ip(_as_str(common_mapping.get("HostIP"))),
        wifi_gateway=_decode_hex_ip(_as_str(wifi_mapping.get("GateWay"))),
        common_gateway=_decode_hex_ip(_as_str(common_mapping.get("GateWay"))),
        tcp_port=_as_int(common_mapping.get("TCPPort")),
        udp_port=_as_int(common_mapping.get("UDPPort")),
        http_port=_as_int(common_mapping.get("HttpPort")),
        ssl_port=_as_int(common_mapping.get("SSLPort")),
        ssid=_as_str(wifi_mapping.get("SSID")),
        mac=_as_str(common_mapping.get("MAC")),
    )


def fetch_camera_encode_info(
    camera_ip: str, username: str, password: str
) -> CameraEncodeInfo:
    """Fetch main and extra encode profiles from the local camera."""
    with _camera_session(camera_ip, username, password) as camera:
        data = camera.get_command("Simplify.Encode", 1042)

    config = data[0] if isinstance(data, list) and data else {}
    config_mapping = config if isinstance(config, Mapping) else {}
    return CameraEncodeInfo(
        main=_parse_encode_profile(_mapping(config_mapping.get("MainFormat"))),
        extra=_parse_encode_profile(_mapping(config_mapping.get("ExtraFormat"))),
    )


def fetch_camera_storage_info(
    camera_ip: str, username: str, password: str
) -> CameraStorageInfo:
    """Fetch SD-card/storage information from the local camera."""
    with _camera_session(camera_ip, username, password) as camera:
        data = camera.get_command("StorageInfo", 1020)

    partitions: list[CameraStoragePartition] = []
    if isinstance(data, list):
        for item in data:
            item_mapping = _mapping(item)
            for partition in _list(item_mapping.get("Partition")):
                partition_mapping = _mapping(partition)
                partitions.append(
                    CameraStoragePartition(
                        driver_type=int(partition_mapping.get("DirverType", 0)),
                        is_current=bool(partition_mapping.get("IsCurrent", False)),
                        total_space=_as_str(partition_mapping.get("TotalSpace")) or "",
                        remain_space=(
                            _as_str(partition_mapping.get("RemainSpace")) or ""
                        ),
                        start_time=_as_str(partition_mapping.get("NewStartTime")) or "",
                        end_time=_as_str(partition_mapping.get("NewEndTime")) or "",
                    )
                )
    return CameraStorageInfo(partitions=partitions)


def fetch_camera_timelapse_config(
    camera_ip: str, username: str, password: str
) -> CameraTimelapseConfig | None:
    """Fetch the timelapse/epitome recording configuration."""
    with _camera_session(camera_ip, username, password) as camera:
        data = camera.get_command("Storage.EpitomeRecord", 1042)

    if not isinstance(data, list) or not data:
        return None
    config = _mapping(data[0])
    return CameraTimelapseConfig(
        enabled=bool(config.get("Enable", False)),
        interval_seconds=int(config.get("Interval", 0)),
        start_time=_as_str(config.get("StartTime")) or "",
        end_time=_as_str(config.get("EndTime")) or "",
        time_sections=[str(section) for section in _list(config.get("TimeSection"))],
    )


def fetch_camera_snapshot(camera_ip: str, username: str, password: str) -> bytes:
    """Fetch a current JPEG snapshot from the local camera."""
    with _camera_session(camera_ip, username, password) as camera:
        image = camera.snapshot(0)
    if not image:
        raise ConnectionError("Camera snapshot returned no data")
    return bytes(image)


def fetch_camera_time_settings(
    camera_ip: str, username: str, password: str
) -> CameraTimeSettings:
    """Fetch timezone and current device time from the local camera."""
    with _camera_session(camera_ip, username, password) as camera:
        return _fetch_camera_time_settings_from_camera(camera)


def update_camera_time_settings(
    camera_ip: str,
    username: str,
    password: str,
    *,
    time_format: str | None = None,
    date_format: str | None = None,
) -> CameraTimeSettings:
    """Update camera date/time display settings."""
    with _camera_session(camera_ip, username, password) as camera:
        location = dict(_mapping(camera.get_command("General.Location", 1042)))
        if time_format is not None:
            location["TimeFormat"] = time_format
        if date_format is not None:
            location["DateFormat"] = date_format
        camera.set_info("General.Location", location)
        return _fetch_camera_time_settings_from_camera(camera)


def fetch_camera_overlay_settings(
    camera_ip: str, username: str, password: str
) -> CameraOverlaySettings:
    """Fetch timestamp/logo overlay visibility settings from the camera."""
    with _camera_session(camera_ip, username, password) as camera:
        logo = _mapping(camera.get_command("fVideo.OsdLogo", 1042))
        widget = _mapping(camera.get_command("AVEnc.VideoWidget[0]", 1042))

    time_attr = _mapping(widget.get("TimeTitleAttribute"))
    timestamp_enabled = bool(
        time_attr.get("EncodeBlend") or time_attr.get("PreviewBlend")
    )
    logo_enabled = bool(logo.get("Enable", False))
    return CameraOverlaySettings(
        timestamp_enabled=timestamp_enabled,
        logo_enabled=logo_enabled,
    )


def fetch_camera_status_led(
    camera_ip: str, username: str, password: str
) -> bool | None:
    """Fetch the current status LED/indicator light state."""
    with _camera_session(camera_ip, username, password) as camera:
        data = _mapping(camera.get_command("FbExtraStateCtrl", 1042))
    return _as_bool(data.get("ison"))


def update_camera_status_led(
    camera_ip: str,
    username: str,
    password: str,
    enabled: bool,
) -> bool | None:
    """Enable or disable the camera status LED/indicator light."""
    with _camera_session(camera_ip, username, password) as camera:
        data = dict(_mapping(camera.get_command("FbExtraStateCtrl", 1042)))
        data["ison"] = 1 if enabled else 0
        camera.set_info("FbExtraStateCtrl", data)
        updated = _mapping(camera.get_command("FbExtraStateCtrl", 1042))
    return _as_bool(updated.get("ison"))


def fetch_camera_diagnostics(
    camera_ip: str, username: str, password: str
) -> CameraDiagnostics:
    """Fetch a user-facing diagnostics summary from the local camera."""
    with _camera_session(camera_ip, username, password) as camera:
        system_info = _mapping(camera.get_system_info())
        network = fetch_camera_network_info(camera_ip, username, password)
        overlays = fetch_camera_overlay_settings(camera_ip, username, password)
        time_settings = _fetch_camera_time_settings_from_camera(camera)
        wifi_route = _mapping(camera.get_command("WifiRouteInfo", 1020))
        camera_blob = _mapping(camera.get_command("Camera", 1042))
        camera_param_list = _list(camera_blob.get("Param"))
        camera_param = _mapping(camera_param_list[0]) if camera_param_list else {}
        white_light = _mapping(camera_blob.get("WhiteLight"))
        storage = fetch_camera_storage_info(camera_ip, username, password)

    sd_partition = next(
        (partition for partition in storage.partitions if partition.driver_type == 0),
        None,
    )
    sd_total_mb = _parse_hex_int(sd_partition.total_space) if sd_partition else None
    sd_free_mb = _parse_hex_int(sd_partition.remain_space) if sd_partition else None
    sd_used_mb = (
        sd_total_mb - sd_free_mb
        if sd_total_mb is not None and sd_free_mb is not None
        else None
    )

    return CameraDiagnostics(
        device_model=_as_str(system_info.get("DeviceModel")),
        hardware=_as_str(system_info.get("HardWare")),
        hardware_version=_as_str(system_info.get("HardWareVersion")),
        firmware_version=_as_str(system_info.get("SoftWareVersion")),
        serial_number=_as_str(system_info.get("SerialNo")),
        mac_address=network.mac,
        wifi_mac_address=_as_str(wifi_route.get("WlanMac")),
        wifi_ip=network.wifi_ip,
        common_ip=network.common_ip,
        wlan_ssid=network.ssid,
        signal_strength=_as_int(wifi_route.get("SignalLevel")),
        timezone_offset_minutes=time_settings.timezone_offset_minutes,
        current_time=time_settings.device_time,
        date_format=time_settings.date_format,
        time_format=time_settings.time_format,
        sd_total_mb=sd_total_mb,
        sd_used_mb=sd_used_mb,
        sd_free_mb=sd_free_mb,
        timestamp_enabled=overlays.timestamp_enabled,
        logo_enabled=overlays.logo_enabled,
        status_led_enabled=fetch_camera_status_led(camera_ip, username, password),
        picture_mirror=_as_bool(camera_param.get("PictureMirror")),
        picture_flip=_as_bool(camera_param.get("PictureFlip")),
        night_mode=_night_mode_from_param(camera_param),
        white_light_mode=_as_str(white_light.get("WorkMode")),
    )


def update_camera_overlay_settings(
    camera_ip: str,
    username: str,
    password: str,
    *,
    timestamp_enabled: bool | None = None,
    logo_enabled: bool | None = None,
) -> CameraOverlaySettings:
    """Enable or disable camera timestamp/logo overlays."""
    with _camera_session(camera_ip, username, password) as camera:
        logo = dict(_mapping(camera.get_command("fVideo.OsdLogo", 1042)))
        widget = dict(_mapping(camera.get_command("AVEnc.VideoWidget[0]", 1042)))
        time_attr = dict(_mapping(widget.get("TimeTitleAttribute")))

        if logo_enabled is not None:
            logo["Enable"] = logo_enabled
            camera.set_info("fVideo.OsdLogo", logo)

        if timestamp_enabled is not None:
            time_attr["EncodeBlend"] = timestamp_enabled
            time_attr["PreviewBlend"] = timestamp_enabled
            widget["TimeTitleAttribute"] = time_attr
            camera.set_info("AVEnc.VideoWidget[0]", widget)

        return CameraOverlaySettings(
            timestamp_enabled=bool(
                time_attr.get("EncodeBlend") or time_attr.get("PreviewBlend")
            ),
            logo_enabled=bool(logo.get("Enable", False)),
        )


def sync_camera_clock(
    camera_ip: str,
    username: str,
    password: str,
    *,
    when: datetime | None = None,
    timezone_offset_minutes: int | None = None,
) -> bool:
    """Sync the camera timezone offset and device clock."""
    if when is None:
        when = datetime.now().astimezone()
    if timezone_offset_minutes is None:
        offset = when.utcoffset()
        timezone_offset_minutes = int(offset.total_seconds() // 60) if offset else 0

    target_time = when.astimezone().replace(tzinfo=None) if when.tzinfo else when

    with _camera_session(camera_ip, username, password) as camera:
        current = _fetch_camera_time_settings_from_camera(camera)
        if (
            current.device_time is not None
            and _timezone_matches(
                current.timezone_offset_minutes,
                timezone_offset_minutes,
            )
            and abs((current.device_time - target_time).total_seconds()) <= 60
        ):
            return False
        camera.set_info("System.TimeZone", {"timeMin": timezone_offset_minutes})
        camera.set_time(when)
        return True


def list_camera_recordings(
    camera_ip: str,
    username: str,
    password: str,
    *,
    start_time: datetime,
    end_time: datetime,
    event: str = "*",
) -> list[CameraRecording]:
    """List recordings from the local camera over DVRIP."""
    if end_time < start_time:
        raise InvalidParameterError(
            "end_time must be greater than or equal to start_time"
        )

    payload = {
        "BeginTime": start_time.strftime(_DATE_FORMAT),
        "EndTime": end_time.strftime(_DATE_FORMAT),
        "HighChannel": 0,
        "LowChannel": 1,
        "HighStreamType": "0x00000000",
        "LowStreamType": "0x00000000",
        "Sync": 0,
        "Type": "h264",
        "Event": event,
    }

    with _camera_session(camera_ip, username, password) as camera:
        response = camera.set_command("OPFileQuery", payload, code=_OP_FILE_QUERY_CODE)

    items = _list(_mapping(response).get("OPFileQuery"))
    recordings: list[CameraRecording] = []
    for item in items:
        item_mapping = _mapping(item)
        file_name = _as_str(item_mapping.get("FileName")) or ""
        recordings.append(
            CameraRecording(
                start_time=datetime.strptime(
                    _as_str(item_mapping.get("BeginTime")) or "1970-01-01 00:00:00",
                    _DATE_FORMAT,
                ),
                end_time=datetime.strptime(
                    _as_str(item_mapping.get("EndTime")) or "1970-01-01 00:00:00",
                    _DATE_FORMAT,
                ),
                file_name=file_name,
                length_bytes=int(_as_str(item_mapping.get("FileLength")) or "0", 16),
                disk_no=int(item_mapping.get("DiskNo", 0)),
                category=_parse_recording_category(file_name),
            )
        )
    return recordings


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        if value in {"0", "0x00000000"}:
            return False
        if value in {"1", "0x00000001"}:
            return True
    return None


def _timezone_matches(current: int | None, desired: int | None) -> bool:
    if current is None or desired is None:
        return False
    return current == desired or current == -desired


def _parse_hex_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _night_mode_from_param(data: Mapping[str, Any]) -> str | None:
    mode = _as_str(data.get("DayNightColor"))
    if mode is None:
        return None
    return {
        "0x00000000": "auto",
        "0x00000001": "day",
        "0x00000002": "night",
    }.get(mode)


def _fetch_camera_time_settings_from_camera(camera: Any) -> CameraTimeSettings:
    location = _mapping(camera.get_command("General.Location", 1042))
    timezone_info = _mapping(camera.get_command("System.TimeZone", 1042))
    current_time = camera.get_command("OPTimeQuery", 1452)
    device_time = None
    if isinstance(current_time, str):
        device_time = datetime.strptime(current_time, _DATE_FORMAT)
    return CameraTimeSettings(
        timezone_offset_minutes=_as_int(timezone_info.get("timeMin")),
        device_time=device_time,
        date_format=_as_str(location.get("DateFormat")),
        time_format=_as_str(location.get("TimeFormat")),
        dst_rule=_as_str(location.get("DSTRule")),
    )
