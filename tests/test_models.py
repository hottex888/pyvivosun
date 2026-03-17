"""Tests for data models."""

from datetime import UTC, datetime

from pyvivosun.models import (
    AwsCredentials,
    CirculationFanState,
    Device,
    DeviceType,
    DuctFanState,
    EventType,
    HeaterState,
    HumidifierState,
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
        assert s.outside_temperature is None
        assert s.outside_humidity is None
        assert s.outside_vpd is None
        assert s.core_temperature is None
        assert s.rssi is None
        assert s.water_level is None

    def test_all_fields(self) -> None:
        s = SensorData(
            temperature=25.0,
            humidity=60.0,
            vpd=1.2,
            outside_temperature=22.0,
            outside_humidity=55.0,
            outside_vpd=1.0,
            core_temperature=35.0,
            rssi=-45,
            water_level=80,
        )
        assert s.outside_temperature == 22.0
        assert s.rssi == -45
        assert s.water_level == 80


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


class TestHumidifierState:
    def test_defaults(self) -> None:
        hs = HumidifierState()
        assert hs.on is False
        assert hs.target_humidity is None

    def test_with_target(self) -> None:
        hs = HumidifierState(on=True, level=5, mode=1, target_humidity=65.0)
        assert hs.target_humidity == 65.0


class TestHeaterState:
    def test_defaults(self) -> None:
        hs = HeaterState()
        assert hs.on is False
        assert hs.target_temp is None
        assert hs.state == 0

    def test_with_target(self) -> None:
        hs = HeaterState(on=True, level=3, mode=1, state=1, target_temp=28.0)
        assert hs.target_temp == 28.0
        assert hs.state == 1


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

    def test_humidifier_auto_target(self) -> None:
        shadow = {
            "state": {
                "reported": {
                    "hmdf": {
                        "on": 1,
                        "lv": 5,
                        "mode": 1,
                        "waterWarn": 0,
                        "targetHumi": 6500,
                    }
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.humidifier.on is True
        assert state.humidifier.mode == 1
        assert state.humidifier.target_humidity == 65.0

    def test_humidifier_manual_nested_level(self) -> None:
        """Level can be nested under manu.lv when top-level lv is absent."""
        shadow = {
            "state": {
                "reported": {
                    "hmdf": {"on": 1, "mode": 0, "manu": {"lv": 7}},
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.humidifier.level == 7

    def test_humidifier_top_level_lv_preferred(self) -> None:
        """Top-level lv takes precedence over manu.lv."""
        shadow = {
            "state": {
                "reported": {
                    "hmdf": {"on": 1, "lv": 3, "mode": 0, "manu": {"lv": 7}},
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.humidifier.level == 3

    def test_heater_auto_target(self) -> None:
        shadow = {
            "state": {
                "reported": {
                    "heat": {
                        "on": 1,
                        "lv": 4,
                        "mode": 1,
                        "state": 1,
                        "targetTemp": 2800,
                    }
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.heater.on is True
        assert state.heater.mode == 1
        assert state.heater.state == 1
        assert state.heater.target_temp == 28.0

    def test_heater_manual_nested_level(self) -> None:
        shadow = {
            "state": {
                "reported": {
                    "heat": {"on": 1, "mode": 0, "state": 0, "manu": {"lv": 6}},
                }
            }
        }
        state = parse_shadow_to_state("dev1", shadow)
        assert state.heater.level == 6
