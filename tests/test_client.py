"""Tests for VivosunClient facade."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pyvivosun.client import VivosunClient, _EventBus
from pyvivosun.exceptions import DeviceNotFoundError, InvalidParameterError
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
