"""Tests for data models."""

from datetime import UTC, datetime

from pyvivosun.models import (
    AwsCredentials,
    CirculationFanState,
    Device,
    DeviceType,
    DuctFanState,
    EventType,
    LightState,
    SensorData,
    TokenSet,
    VivosunEvent,
    parse_shadow_to_state,
)


class TestTokenSet:
    def test_creation(self) -> None:
        ts = TokenSet(
            access_token="at",
            login_token="lt",
            refresh_token="rt",
            user_id="uid",
        )
        assert ts.access_token == "at"
        assert ts.user_id == "uid"


class TestAwsCredentials:
    def test_creation(self) -> None:
        creds = AwsCredentials(
            host="iot.us-east-1.amazonaws.com",
            region="us-east-1",
            access_key_id="AKIA...",
            secret_access_key="secret",
            session_token="token",
            port=443,
            expires_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert creds.region == "us-east-1"
        assert creds.port == 443


class TestDevice:
    def test_creation(self) -> None:
        d = Device(
            device_id="d1",
            client_id="c1",
            name="My Tent",
            device_type=DeviceType.CONTROLLER,
            topic_prefix="prefix",
            scene_id="s1",
            online=True,
        )
        assert d.online is True
        assert d.model is None

    def test_with_model(self) -> None:
        d = Device(
            device_id="d1",
            client_id="c1",
            name="My Tent",
            device_type=DeviceType.LIGHT,
            topic_prefix="prefix",
            scene_id="s1",
            online=False,
            model="E42A",
        )
        assert d.model == "E42A"


class TestDeviceType:
    def test_values(self) -> None:
        assert DeviceType.CONTROLLER == "controller"
        assert DeviceType.UNKNOWN == "unknown"


class TestSensorData:
    def test_defaults(self) -> None:
        s = SensorData()
        assert s.temperature is None
        assert s.humidity is None
        assert s.vpd is None


class TestLightState:
    def test_defaults(self) -> None:
        ls = LightState()
        assert ls.on is False
        assert ls.level == 0


class TestCirculationFanState:
    def test_defaults(self) -> None:
        cfs = CirculationFanState()
        assert cfs.natural_wind is False


class TestDuctFanState:
    def test_defaults(self) -> None:
        dfs = DuctFanState()
        assert dfs.auto_mode is False
        assert dfs.target_temp is None


class TestEventType:
    def test_values(self) -> None:
        assert EventType.STATE_CHANGED == "state_changed"
        assert EventType.DEVICE_ONLINE == "device_online"


class TestVivosunEvent:
    def test_creation(self) -> None:
        e = VivosunEvent(event_type=EventType.STATE_CHANGED, device_id="d1")
        assert e.data is None


class TestParseShadowToState:
    def test_full_shadow(self) -> None:
        """Shadows contain control state only — no sensor data."""
        shadow = {
            "state": {
                "reported": {
                    "light": {"on": 1, "lv": 75, "mode": 1, "spectrum": 0},
                    "cFan": {"on": 1, "lv": 5, "oscillation": 1, "nightMode": 0},
                    "dFan": {
                        "on": 1,
                        "lv": 7,
                        "auto": 1,
                        "targetTemp": 2800,
                        "targetHumi": 5500,
                    },
                    "hmdf": {"on": 1, "lv": 3, "mode": 0, "waterWarn": 1},
                    "heat": {"on": 1, "lv": 2, "mode": 0, "state": 1},
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.device_id == "dev1"
        # Sensors are empty — they come from REST only
        assert state.sensors.temperature is None
        assert state.light.on is True
        assert state.light.level == 75
        assert state.circulation_fan.on is True
        assert state.circulation_fan.level == 5
        assert state.circulation_fan.oscillation is True
        assert state.duct_fan.on is True
        assert state.duct_fan.level == 7
        assert state.duct_fan.auto_mode is True
        assert state.duct_fan.target_temp == 28.0
        assert state.duct_fan.target_humidity == 55.0
        assert state.humidifier.on is True
        assert state.humidifier.level == 3
        assert state.humidifier.water_warning is True
        assert state.heater.on is True
        assert state.heater.level == 2
        assert state.heater.state == 1

    def test_no_sensor_data_in_shadow(self) -> None:
        """Even if shadow has temp/humi fields, we don't parse them as sensors."""
        shadow = {
            "state": {
                "reported": {
                    "temp": 2500,
                    "humi": 6000,
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.sensors.temperature is None
        assert state.sensors.humidity is None

    def test_flat_reported_control_state(self) -> None:
        """Delta updates come without the state.reported wrapper."""
        shadow = {"light": {"on": 1, "lv": 50}}
        state = parse_shadow_to_state("dev1", shadow)
        assert state.light.on is True
        assert state.light.level == 50

    def test_natural_wind(self) -> None:
        shadow = {
            "state": {
                "reported": {
                    "cFan": {"on": 1, "lv": 200},
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.circulation_fan.natural_wind is True

    def test_empty_shadow(self) -> None:
        state = parse_shadow_to_state("dev1", {})
        assert state.sensors.temperature is None
        assert state.light.on is False

    def test_raw_shadow_preserved(self) -> None:
        shadow = {"state": {"reported": {"light": {"on": 1}}}}
        state = parse_shadow_to_state("dev1", shadow)
        assert state.raw_shadow == shadow
