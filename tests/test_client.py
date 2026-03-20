"""Tests for VivosunClient facade."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pyvivosun.client import VivosunClient, _EventBus
from pyvivosun.exceptions import DeviceNotFoundError, InvalidParameterError
from pyvivosun.models import CameraNetworkInfo, RpsStatus
from pyvivosun.models.device import DeviceType
from pyvivosun.models.event import EventType, VivosunEvent

# --- Fixtures ---


@pytest.fixture
def mock_rest():
    with patch("pyvivosun.client.RestClient") as MockRest:
        rest = MockRest.return_value
        rest.login = AsyncMock(
            return_value={
                "accessToken": "at",
                "loginToken": "lt",
                "refreshToken": "rt",
                "userId": 1,
            }
        )
        rest.get_device_list = AsyncMock(
            return_value=[
                {
                    "deviceId": "d1",
                    "clientId": "c1",
                    "name": "GrowHub Controller",
                    "topicPrefix": "pfx",
                    "scene": {"sceneId": 108080},
                    "onlineStatus": 1,
                },
            ]
        )
        rest.get_aws_identity = AsyncMock(
            return_value={
                "awsHost": "iot.example.com",
                "awsRegion": "us-east-2",
                "awsIdentityId": "id123",
                "awsOpenIdToken": "tok123",
                "awsPort": 443,
            }
        )
        rest.get_cognito_credentials = AsyncMock(
            return_value={
                "Credentials": {
                    "AccessKeyId": "AKIA",
                    "SecretKey": "secret",
                    "SessionToken": "token",
                    "Expiration": 9999999999.0,
                },
                "IdentityId": "id123",
            }
        )
        rest.get_point_log = AsyncMock(
            return_value=[{"inTemp": 2500, "inHumi": 6000, "inVpd": 120}]
        )
        rest._ensure_session = AsyncMock(return_value=object())
        rest.close = AsyncMock()
        yield rest


@pytest.fixture
def mock_mqtt():
    with patch("pyvivosun.client.MqttClient") as MockMqtt:
        mqtt = MockMqtt.return_value
        mqtt.connect = AsyncMock()
        mqtt.disconnect = AsyncMock()
        mqtt.publish_shadow_update = AsyncMock()
        yield mqtt


@pytest.fixture
def client(mock_rest, mock_mqtt):
    return VivosunClient("user@example.com", "password")


# --- Tests ---


class TestConnect:
    async def test_authenticates_and_discovers(self, client, mock_rest) -> None:
        await client.connect()
        mock_rest.login.assert_called_once()
        mock_rest.get_device_list.assert_called_once()

    async def test_connects_mqtt(self, client, mock_mqtt) -> None:
        await client.connect()
        mock_mqtt.connect.assert_called_once_with(["c1"])

    async def test_context_manager(self, mock_rest, mock_mqtt) -> None:
        async with VivosunClient("u@e.com", "p") as c:
            devices = await c.get_devices()
            assert len(devices) == 1
        mock_mqtt.disconnect.assert_called_once()


class TestDiscovery:
    async def test_get_devices(self, client) -> None:
        await client.connect()
        devices = await client.get_devices()
        assert len(devices) == 1
        assert devices[0].device_id == "d1"
        assert devices[0].device_type == DeviceType.CONTROLLER

    async def test_get_device_by_id(self, client) -> None:
        await client.connect()
        device = await client.get_device("d1")
        assert device is not None
        assert device.name == "GrowHub Controller"

    async def test_get_device_not_found(self, client) -> None:
        await client.connect()
        device = await client.get_device("nonexistent")
        assert device is None

    async def test_discovery_extracts_camera_credentials(
        self, mock_rest, mock_mqtt
    ) -> None:
        mock_rest.get_device_list.return_value = [
            {
                "deviceId": "cam1",
                "clientId": "",
                "hwId": "5a8ddedd3c1e7674",
                "name": "GrowCam C4",
                "topicPrefix": "",
                "scene": {"sceneId": 108080},
                "onlineStatus": 1,
                "setting": {"jf": {"devUser": "abjd", "devPass": "4kt5em"}},
            },
        ]
        client = VivosunClient("user@example.com", "password")

        await client.connect()
        device = await client.get_device("cam1")

        assert device is not None
        assert device.camera_username == "abjd"
        assert device.camera_password == "4kt5em"


class TestState:
    async def test_get_state_returns_none_initially(self, client) -> None:
        await client.connect()
        state = client.get_state("d1")
        assert state is None

    async def test_get_state_after_shadow_update(self, client) -> None:
        await client.connect()
        # Simulate a shadow update with control state (shadows have no sensor data)
        await client._on_shadow_update(
            "c1",
            {"state": {"reported": {"light": {"on": 1, "lv": 75}}}},
        )
        state = client.get_state("d1")
        assert state is not None
        assert state.light.on is True
        assert state.light.level == 75
        # Sensors stay None — they come from REST only
        assert state.sensors.temperature is None

    async def test_shadow_merge_preserves_existing(self, client) -> None:
        await client.connect()
        await client._on_shadow_update(
            "c1",
            {
                "state": {
                    "reported": {
                        "light": {"on": 1, "lv": 75},
                        "dFan": {"on": 1, "lv": 5},
                    }
                }
            },
        )
        # Delta with only light change
        await client._on_shadow_update(
            "c1",
            {"state": {"reported": {"light": {"on": 0, "lv": 0}}}},
        )
        state = client.get_state("d1")
        assert state.light.on is False
        assert state.light.level == 0


class TestSensorData:
    async def test_get_sensor_data(self, client, mock_rest) -> None:
        await client.connect()
        data = await client.get_sensor_data("d1")
        assert data is not None
        assert data.temperature == 25.0
        assert data.humidity == 60.0
        assert data.vpd == 1.2

    async def test_get_sensor_data_not_found(self, client) -> None:
        await client.connect()
        with pytest.raises(DeviceNotFoundError):
            await client.get_sensor_data("nonexistent")

    async def test_get_sensor_data_probe_keys(self, client, mock_rest) -> None:
        """Humidifier/heater use pTemp/pHumi/pVpd."""
        mock_rest.get_point_log.return_value = [
            {"pTemp": 2200, "pHumi": 7000, "pVpd": 90, "waterLv": 80}
        ]
        await client.connect()
        data = await client.get_sensor_data("d1")
        assert data.temperature == 22.0
        assert data.humidity == 70.0
        assert data.vpd == 0.9
        assert data.water_level == 80

    async def test_get_sensor_data_all_fields(self, client, mock_rest) -> None:
        """GrowHub returns all sensor fields."""
        mock_rest.get_point_log.return_value = [
            {
                "inTemp": 2500,
                "inHumi": 6000,
                "inVpd": 120,
                "outTemp": 2200,
                "outHumi": 5500,
                "outVpd": 100,
                "coreTemp": 3500,
                "rssi": -45,
            }
        ]
        await client.connect()
        data = await client.get_sensor_data("d1")
        assert data.temperature == 25.0
        assert data.outside_temperature == 22.0
        assert data.outside_humidity == 55.0
        assert data.outside_vpd == 1.0
        assert data.core_temperature == 35.0
        assert data.rssi == -45

    async def test_get_sensor_data_sentinel_filtered(self, client, mock_rest) -> None:
        """Sentinel values (-6666) are treated as missing."""
        mock_rest.get_point_log.return_value = [
            {"inTemp": -6666, "inHumi": 6000, "outTemp": -6666}
        ]
        await client.connect()
        data = await client.get_sensor_data("d1")
        assert data.temperature is None
        assert data.humidity == 60.0
        assert data.outside_temperature is None

    async def test_get_sensor_data_zero_temp(self, client, mock_rest) -> None:
        """Zero is a valid reading (0.00), not treated as missing."""
        mock_rest.get_point_log.return_value = [{"inTemp": 0, "inHumi": 0}]
        await client.connect()
        data = await client.get_sensor_data("d1")
        assert data.temperature == 0.0
        assert data.humidity == 0.0


class TestCameraSupport:
    async def test_get_camera_network_info(self, client, mock_rest) -> None:
        mock_rest.get_device_list.return_value = [
            {
                "deviceId": "cam1",
                "clientId": "",
                "name": "GrowCam C4",
                "topicPrefix": "",
                "scene": {"sceneId": 108080},
                "onlineStatus": 1,
                "setting": {"jf": {"devUser": "abjd", "devPass": "4kt5em"}},
            },
        ]
        await client.connect()

        with patch(
            "pyvivosun.client.fetch_camera_network_info",
            return_value=CameraNetworkInfo(wifi_ip="10.0.15.202"),
        ) as fetch_info:
            info = await client.get_camera_network_info("cam1", camera_ip="10.0.15.202")

        assert info.wifi_ip == "10.0.15.202"
        fetch_info.assert_called_once_with("10.0.15.202", "abjd", "4kt5em")

    async def test_get_camera_network_info_requires_camera_device(self, client) -> None:
        await client.connect()

        with pytest.raises(InvalidParameterError):
            await client.get_camera_network_info("d1", camera_ip="10.0.15.202")

    async def test_get_camera_rps_status(self, client, mock_rest) -> None:
        mock_rest.get_device_list.return_value = [
            {
                "deviceId": "cam1",
                "clientId": "",
                "name": "GrowCam C4",
                "topicPrefix": "",
                "scene": {"sceneId": 108080},
                "onlineStatus": 1,
                "setting": {"jf": {"devUser": "abjd", "devPass": "4kt5em"}},
            },
        ]
        await client.connect()

        with patch(
            "pyvivosun.client.query_rps_status",
            new=AsyncMock(
                side_effect=[
                    None,
                    RpsStatus(
                        serial_number="5a8ddedd3c1e7674",
                        status="Online",
                        device_type="Camera",
                        server_ip="3.73.2.109",
                        server_port=6510,
                        device_port=34567,
                        wan_ip="78.94.212.194",
                        kcp_enabled=False,
                    ),
                ]
            ),
        ) as query_status:
            status = await client.get_camera_rps_status(
                "cam1",
                auth_codes=("aaaaaaaa103122aded", "aaaaaaaa-13122aded"),
            )

        assert status is not None
        assert status.server_ip == "3.73.2.109"
        assert query_status.await_count == 2


class TestCommands:
    async def test_set_light(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_light("d1", on=True, level=75)
        mock_mqtt.publish_shadow_update.assert_called_once_with(
            "c1", {"light": {"on": 1, "level": 75}}
        )

    async def test_set_light_clamps_level(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_light("d1", level=10)
        call_args = mock_mqtt.publish_shadow_update.call_args
        assert call_args[0][1]["light"]["level"] == 25  # clamped to min

    async def test_set_light_no_params(self, client) -> None:
        await client.connect()
        with pytest.raises(InvalidParameterError):
            await client.set_light("d1")

    async def test_set_circulation_fan(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_circulation_fan("d1", on=True, level=5)
        call_args = mock_mqtt.publish_shadow_update.call_args
        assert call_args[0][1] == {"cFan": {"on": 1, "level": 5}}

    async def test_set_circulation_fan_natural_wind(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_circulation_fan("d1", natural_wind=True)
        call_args = mock_mqtt.publish_shadow_update.call_args
        assert call_args[0][1]["cFan"]["level"] == 200

    async def test_set_duct_fan(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_duct_fan("d1", on=True, auto_mode=True, target_temp=28.0)
        call_args = mock_mqtt.publish_shadow_update.call_args
        desired = call_args[0][1]["dFan"]
        assert desired["on"] == 1
        assert desired["auto"] == 1
        assert desired["targetTemp"] == 2800

    async def test_set_humidifier_on_off(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_humidifier("d1", on=True)
        call_args = mock_mqtt.publish_shadow_update.call_args
        assert call_args[0][1] == {"hmdf": {"on": 1}}

    async def test_set_humidifier_manual_level(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_humidifier("d1", level=5)
        call_args = mock_mqtt.publish_shadow_update.call_args
        desired = call_args[0][1]["hmdf"]
        assert desired["mode"] == 0  # manual
        assert desired["manu"] == {"lv": 5}

    async def test_set_humidifier_level_clamped(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_humidifier("d1", level=15)
        call_args = mock_mqtt.publish_shadow_update.call_args
        assert call_args[0][1]["hmdf"]["manu"]["lv"] == 10  # clamped

    async def test_set_humidifier_auto_target(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_humidifier("d1", mode=1, target_humidity=65.0)
        call_args = mock_mqtt.publish_shadow_update.call_args
        desired = call_args[0][1]["hmdf"]
        assert desired["mode"] == 1
        assert desired["targetHumi"] == 6500

    async def test_set_humidifier_no_params(self, client) -> None:
        await client.connect()
        with pytest.raises(InvalidParameterError):
            await client.set_humidifier("d1")

    async def test_set_heater_on_off(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_heater("d1", on=True)
        call_args = mock_mqtt.publish_shadow_update.call_args
        assert call_args[0][1] == {"heat": {"on": 1}}

    async def test_set_heater_manual_level(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_heater("d1", level=3)
        call_args = mock_mqtt.publish_shadow_update.call_args
        desired = call_args[0][1]["heat"]
        assert desired["mode"] == 0
        assert desired["manu"] == {"lv": 3}

    async def test_set_heater_level_clamped(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_heater("d1", level=-1)
        call_args = mock_mqtt.publish_shadow_update.call_args
        assert call_args[0][1]["heat"]["manu"]["lv"] == 0  # clamped

    async def test_set_heater_auto_target(self, client, mock_mqtt) -> None:
        await client.connect()
        await client.set_heater("d1", mode=1, target_temp=28.0)
        call_args = mock_mqtt.publish_shadow_update.call_args
        desired = call_args[0][1]["heat"]
        assert desired["mode"] == 1
        assert desired["targetTemp"] == 2800

    async def test_set_heater_no_params(self, client) -> None:
        await client.connect()
        with pytest.raises(InvalidParameterError):
            await client.set_heater("d1")

    async def test_command_device_not_found(self, client) -> None:
        await client.connect()
        with pytest.raises(DeviceNotFoundError):
            await client.set_light("nonexistent", on=True)


class TestEvents:
    async def test_on_state_changed(self, client) -> None:
        await client.connect()
        events: list[VivosunEvent] = []
        unsub = client.on_state_changed(lambda e: events.append(e))

        await client._on_shadow_update(
            "c1", {"state": {"reported": {"temp": 2500}}}
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.STATE_CHANGED
        assert events[0].device_id == "d1"

        unsub()
        await client._on_shadow_update(
            "c1", {"state": {"reported": {"temp": 2600}}}
        )
        assert len(events) == 1  # callback was unsubscribed

    async def test_on_connection_changed(self, client) -> None:
        events: list[VivosunEvent] = []
        client.on_connection_changed(lambda e: events.append(e))

        await client._on_connection_state(True)
        assert len(events) == 1
        assert events[0].event_type == EventType.CONNECTION_CHANGED
        assert events[0].data is True

    async def test_async_callback(self, client) -> None:
        await client.connect()
        events: list[VivosunEvent] = []

        async def async_cb(e: VivosunEvent) -> None:
            events.append(e)

        client.on_state_changed(async_cb)
        await client._on_shadow_update(
            "c1", {"state": {"reported": {"temp": 2500}}}
        )
        assert len(events) == 1


class TestEventBus:
    async def test_emit_no_listeners(self) -> None:
        bus = _EventBus()
        await bus.emit(VivosunEvent(event_type=EventType.STATE_CHANGED))

    async def test_unsubscribe(self) -> None:
        bus = _EventBus()
        calls: list[VivosunEvent] = []
        unsub = bus.on(EventType.STATE_CHANGED, lambda e: calls.append(e))

        await bus.emit(VivosunEvent(event_type=EventType.STATE_CHANGED))
        assert len(calls) == 1

        unsub()
        await bus.emit(VivosunEvent(event_type=EventType.STATE_CHANGED))
        assert len(calls) == 1
