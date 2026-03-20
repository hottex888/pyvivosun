"""Tests for local GrowCam support."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pyvivosun.camera import (
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


class _FakeDVRIPCam:
    def __init__(self, ip: str, **kwargs) -> None:
        self.ip = ip
        self.kwargs = kwargs
        self.snapshot_bytes = b"jpeg-bytes"
        self.info_updates: list[tuple[str, object]] = []
        self.time_updates: list[datetime] = []
        self.timezone_minutes = 60
        self.current_time = datetime(2026, 3, 19, 10, 6, 43)
        self.logo_enabled = False
        self.timestamp_enabled = True
        self.status_led_enabled = False
        self.date_format = "YYMMDD"
        self.time_format = "12"
        self.dst_rule = "On"

    def login(self) -> bool:
        return True

    def close(self) -> None:
        return None

    def set_info(self, name: str, data: object):
        self.info_updates.append((name, data))
        if name == "System.TimeZone":
            assert isinstance(data, dict)
            self.timezone_minutes = int(data["timeMin"])
        if name == "fVideo.OsdLogo":
            assert isinstance(data, dict)
            self.logo_enabled = bool(data["Enable"])
        if name == "AVEnc.VideoWidget[0]":
            assert isinstance(data, dict)
            time_attr = data.get("TimeTitleAttribute", {})
            assert isinstance(time_attr, dict)
            self.timestamp_enabled = bool(
                time_attr.get("EncodeBlend") or time_attr.get("PreviewBlend")
            )
        if name == "General.Location":
            assert isinstance(data, dict)
            if "DateFormat" in data:
                self.date_format = str(data["DateFormat"])
            if "TimeFormat" in data:
                self.time_format = str(data["TimeFormat"])
            if "DSTRule" in data:
                self.dst_rule = str(data["DSTRule"])
        if name == "FbExtraStateCtrl":
            assert isinstance(data, dict)
            self.status_led_enabled = bool(data["ison"])
        return {"Ret": 100}

    def set_time(self, when: datetime):
        self.time_updates.append(when)
        self.current_time = (
            when.astimezone().replace(tzinfo=None) if when.tzinfo else when
        )
        return {"Ret": 100}

    def get_command(self, name: str, code: int | None = None):
        if name == "NetWork.Wifi":
            return {
                "HostIP": "0xCA0F000A",
                "GateWay": "0x010F000A",
                "SSID": "iot.blatz.site",
            }
        if name == "NetWork.NetCommon":
            return {
                "HostIP": "0x0A01A8C0",
                "GateWay": "0x0101A8C0",
                "TCPPort": 34567,
                "UDPPort": 34568,
                "HttpPort": 80,
                "SSLPort": 8443,
                "MAC": "00:12:34:31:78:fe",
            }
        if name == "Simplify.Encode":
            return [
                {
                    "MainFormat": {
                        "Video": {
                            "Compression": "H.265",
                            "BitRate": 2560,
                            "FPS": 15,
                            "Resolution": "4M",
                            "GOP": 3,
                        }
                    },
                    "ExtraFormat": {
                        "Video": {
                            "Compression": "H.265",
                            "BitRate": 1024,
                            "FPS": 15,
                            "Resolution": "D1",
                            "GOP": 1,
                        }
                    },
                }
            ]
        if name == "StorageInfo":
            return [
                {
                    "Partition": [
                        {
                            "DirverType": 0,
                            "IsCurrent": True,
                            "TotalSpace": "0x00002385",
                            "RemainSpace": "0x00000000",
                            "NewStartTime": "2026-02-05 05:39:06",
                            "NewEndTime": "2026-03-18 03:02:49",
                        }
                    ]
                }
            ]
        if name == "Storage.EpitomeRecord":
            return [
                {
                    "Enable": True,
                    "Interval": 14400,
                    "StartTime": "2025-12-13 18:04:42",
                    "EndTime": "2026-06-01 18:00:56",
                    "TimeSection": ["1 00:00:00-23:59:59"],
                }
            ]
        if name == "System.TimeZone":
            return {"timeMin": self.timezone_minutes}
        if name == "General.Location":
            return {
                "DateFormat": self.date_format,
                "TimeFormat": self.time_format,
                "DSTRule": self.dst_rule,
            }
        if name == "WifiRouteInfo" and code == 1020:
            return {
                "SignalLevel": 58,
                "WlanMac": "e8:f4:94:84:34:c1",
                "WlanStatus": True,
                "Eth0Status": False,
            }
        if name == "Camera":
            return {
                "Param": [
                    {
                        "PictureMirror": "0x00000001",
                        "PictureFlip": "0x00000000",
                        "DayNightColor": "0x00000000",
                    }
                ],
                "WhiteLight": {"WorkMode": "Auto"},
            }
        if name == "OPTimeQuery":
            return self.current_time.strftime("%Y-%m-%d %H:%M:%S")
        if name == "fVideo.OsdLogo":
            return {"Enable": self.logo_enabled}
        if name == "AVEnc.VideoWidget[0]":
            return {
                "TimeTitleAttribute": {
                    "EncodeBlend": self.timestamp_enabled,
                    "PreviewBlend": self.timestamp_enabled,
                }
            }
        if name == "FbExtraStateCtrl":
            return {
                "PlayVoiceTip": 1,
                "ison": 1 if self.status_led_enabled else 0,
            }
        raise AssertionError(f"Unexpected get_command call: {name}")

    def get_system_info(self):
        return {
            "DeviceModel": "K80XV40",
            "HardWare": "XM530V200_K80XV40_16M",
            "HardWareVersion": "1.01",
            "SoftWareVersion": "V5.00.R02.00080801.10000.34a432.0000000",
            "SerialNo": "5a8ddedd3c1e7674",
        }

    def snapshot(self, channel: int = 0) -> bytes:
        assert channel == 0
        return self.snapshot_bytes

    def set_command(self, name: str, payload: object, code: int | None = None):
        if name == "OPFileQuery":
            return {
                "Ret": 100,
                "OPFileQuery": [
                    {
                        "BeginTime": "2026-02-24 08:31:34",
                        "EndTime": "2026-02-24 08:40:00",
                        "FileName": (
                            "/idea0/2026-02-24/001/"
                            "08.31.34-08.40.00[R][@10c1][0].h264"
                        ),
                        "FileLength": "0x00024fa4",
                        "DiskNo": 0,
                    },
                    {
                        "BeginTime": "2025-10-16 01:06:54",
                        "EndTime": "2025-12-14 00:23:31",
                        "FileName": (
                            "/idea0/2025-10-16/001/"
                            "01.06.54-00.23.31[E][@589][12].h264"
                        ),
                        "FileLength": "0x0005517d",
                        "DiskNo": 0,
                    },
                ],
            }
        raise AssertionError(f"Unexpected set_command call: {name}")


@pytest.fixture
def fake_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    instances: dict[str, _FakeDVRIPCam] = {}

    def _factory(ip: str, **kwargs) -> _FakeDVRIPCam:
        if ip not in instances:
            instances[ip] = _FakeDVRIPCam(ip, **kwargs)
        return instances[ip]

    monkeypatch.setattr("pyvivosun.camera._get_dvrip_cam_class", lambda: _factory)


def test_fetch_camera_network_info(fake_camera: None) -> None:
    info = fetch_camera_network_info("10.0.15.202", "abjd", "4kt5em")

    assert info.wifi_ip == "10.0.15.202"
    assert info.common_ip == "192.168.1.10"
    assert info.wifi_gateway == "10.0.15.1"
    assert info.common_gateway == "192.168.1.1"
    assert info.tcp_port == 34567
    assert info.ssid == "iot.blatz.site"


def test_fetch_camera_encode_info(fake_camera: None) -> None:
    info = fetch_camera_encode_info("10.0.15.202", "abjd", "4kt5em")

    assert info.main is not None
    assert info.main.codec == "H.265"
    assert info.main.resolution == "4M"
    assert info.extra is not None
    assert info.extra.resolution == "D1"


def test_fetch_camera_storage_info(fake_camera: None) -> None:
    info = fetch_camera_storage_info("10.0.15.202", "abjd", "4kt5em")

    assert len(info.partitions) == 1
    assert info.partitions[0].total_space == "0x00002385"


def test_fetch_camera_timelapse_config(fake_camera: None) -> None:
    config = fetch_camera_timelapse_config("10.0.15.202", "abjd", "4kt5em")

    assert config is not None
    assert config.enabled is True
    assert config.interval_seconds == 14400


def test_fetch_camera_time_settings(fake_camera: None) -> None:
    settings = fetch_camera_time_settings("10.0.15.202", "abjd", "4kt5em")

    assert settings.timezone_offset_minutes == 60
    assert settings.date_format == "YYMMDD"
    assert settings.time_format == "12"
    assert settings.dst_rule == "On"
    assert settings.device_time == datetime(2026, 3, 19, 10, 6, 43)


def test_fetch_camera_overlay_settings(fake_camera: None) -> None:
    settings = fetch_camera_overlay_settings("10.0.15.202", "abjd", "4kt5em")

    assert settings.timestamp_enabled is True
    assert settings.logo_enabled is False


def test_fetch_camera_diagnostics(fake_camera: None) -> None:
    info = fetch_camera_diagnostics("10.0.15.202", "abjd", "4kt5em")

    assert info.device_model == "K80XV40"
    assert info.hardware_version == "1.01"
    assert info.mac_address == "00:12:34:31:78:fe"
    assert info.wifi_mac_address == "e8:f4:94:84:34:c1"
    assert info.wifi_ip == "10.0.15.202"
    assert info.wlan_ssid == "iot.blatz.site"
    assert info.signal_strength == 58
    assert info.timezone_offset_minutes == 60
    assert info.time_format == "12"
    assert info.sd_total_mb == int("2385", 16)
    assert info.sd_used_mb == int("2385", 16)
    assert info.sd_free_mb == 0
    assert info.timestamp_enabled is True
    assert info.logo_enabled is False
    assert info.status_led_enabled is False
    assert info.picture_mirror is True
    assert info.picture_flip is False
    assert info.night_mode == "auto"
    assert info.white_light_mode == "Auto"


def test_update_camera_overlay_settings(fake_camera: None) -> None:
    settings = update_camera_overlay_settings(
        "10.0.15.202",
        "abjd",
        "4kt5em",
        timestamp_enabled=False,
        logo_enabled=True,
    )

    assert settings.timestamp_enabled is False
    assert settings.logo_enabled is True


def test_fetch_camera_status_led(fake_camera: None) -> None:
    assert fetch_camera_status_led("10.0.15.202", "abjd", "4kt5em") is False


def test_update_camera_status_led(fake_camera: None) -> None:
    assert update_camera_status_led("10.0.15.202", "abjd", "4kt5em", True) is True
    assert fetch_camera_status_led("10.0.15.202", "abjd", "4kt5em") is True


def test_update_camera_time_settings(fake_camera: None) -> None:
    settings = update_camera_time_settings(
        "10.0.15.202",
        "abjd",
        "4kt5em",
        time_format="24",
        date_format="DDMMYY",
    )

    assert settings.time_format == "24"
    assert settings.date_format == "DDMMYY"


def test_fetch_camera_snapshot(fake_camera: None) -> None:
    image = fetch_camera_snapshot("10.0.15.202", "abjd", "4kt5em")

    assert image == b"jpeg-bytes"


def test_list_camera_recordings(fake_camera: None) -> None:
    recordings = list_camera_recordings(
        "10.0.15.202",
        "abjd",
        "4kt5em",
        start_time=datetime(2025, 3, 17, 0, 0, 0),
        end_time=datetime(2026, 3, 17, 23, 59, 59),
        event="*",
    )

    assert len(recordings) == 2
    assert recordings[0].category == "regular"
    assert recordings[0].length_bytes == int("24fa4", 16)
    assert recordings[1].category == "event"


def test_sync_camera_clock_sets_timezone_and_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakeDVRIPCam] = []

    def _factory(ip: str, **kwargs) -> _FakeDVRIPCam:
        cam = _FakeDVRIPCam(ip, **kwargs)
        created.append(cam)
        return cam

    monkeypatch.setattr("pyvivosun.camera._get_dvrip_cam_class", lambda: _factory)

    when = datetime(2026, 3, 19, 18, 5, 0, tzinfo=UTC)
    sync_camera_clock(
        "10.0.15.202",
        "abjd",
        "4kt5em",
        when=when,
        timezone_offset_minutes=60,
    )

    camera = created[0]
    assert camera.info_updates == [("System.TimeZone", {"timeMin": 60})]
    assert camera.time_updates == [when]


def test_sync_camera_clock_skips_when_already_correct(fake_camera: None) -> None:
    changed = sync_camera_clock(
        "10.0.15.202",
        "abjd",
        "4kt5em",
        when=datetime(2026, 3, 19, 9, 7, 0, tzinfo=UTC),
        timezone_offset_minutes=60,
    )

    assert changed is False
