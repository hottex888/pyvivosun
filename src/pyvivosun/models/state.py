"""Device state models and shadow parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..util import scale_value


@dataclass
class SensorData:
    """Sensor readings from a device."""

    temperature: float | None = None
    humidity: float | None = None
    vpd: float | None = None


@dataclass
class LightState:
    """State of the light controller."""

    on: bool = False
    level: int = 0
    mode: int = 0
    spectrum: int = 0


@dataclass
class CirculationFanState:
    """State of the circulation fan."""

    on: bool = False
    level: int = 0
    oscillation: bool = False
    night_mode: bool = False
    natural_wind: bool = False


@dataclass
class DuctFanState:
    """State of the duct fan."""

    on: bool = False
    level: int = 0
    auto_mode: bool = False
    target_temp: float | None = None
    target_humidity: float | None = None


@dataclass
class HumidifierState:
    """State of the humidifier (AeroStream H19)."""

    on: bool = False
    level: int = 0
    mode: int = 0
    water_warning: bool = False


@dataclass
class HeaterState:
    """State of the heater (AeroFlux W70)."""

    on: bool = False
    level: int = 0
    mode: int = 0
    state: int = 0  # 0=off, 1=heating


@dataclass
class DeviceState:
    """Complete state of a device, parsed from its shadow."""

    device_id: str
    sensors: SensorData = field(default_factory=SensorData)
    light: LightState = field(default_factory=LightState)
    circulation_fan: CirculationFanState = field(default_factory=CirculationFanState)
    duct_fan: DuctFanState = field(default_factory=DuctFanState)
    humidifier: HumidifierState = field(default_factory=HumidifierState)
    heater: HeaterState = field(default_factory=HeaterState)
    raw_shadow: dict[str, Any] = field(default_factory=dict)
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_shadow_to_state(device_id: str, shadow: dict[str, Any]) -> DeviceState:
    """Parse an AWS IoT shadow document into a DeviceState.

    Handles both full shadow responses (with 'state.reported') and
    delta updates (flat reported dict).

    Note: Shadows contain only control state (light, fans, humidifier, heater).
    Sensor data (temp, humidity, VPD) comes exclusively from REST getPointLog.
    """
    reported = shadow
    if "state" in shadow and "reported" in shadow["state"]:
        reported = shadow["state"]["reported"]

    # Sensors are NOT in shadows — they come from REST only.
    # We still create the SensorData container for merging in the client.
    sensors = SensorData()

    light = LightState()
    if "light" in reported:
        ld = reported["light"]
        light.on = bool(ld.get("on", 0))
        light.level = ld.get("lv", ld.get("level", 0))
        light.mode = ld.get("mode", 0)
        light.spectrum = ld.get("spectrum", 0)

    cfan = CirculationFanState()
    if "cFan" in reported:
        cd = reported["cFan"]
        cfan.on = bool(cd.get("on", 0))
        cfan.level = cd.get("lv", cd.get("level", 0))
        cfan.oscillation = bool(cd.get("oscillation", 0))
        cfan.night_mode = bool(cd.get("nightMode", 0))
        cfan.natural_wind = cd.get("lv", cd.get("level", 0)) == 200

    dfan = DuctFanState()
    if "dFan" in reported:
        dd = reported["dFan"]
        dfan.on = bool(dd.get("on", 0))
        dfan.level = dd.get("lv", dd.get("level", 0))
        dfan.auto_mode = bool(dd.get("auto", 0))
        raw_target_temp = dd.get("targetTemp")
        if raw_target_temp is not None:
            dfan.target_temp = scale_value(raw_target_temp)
        raw_target_humi = dd.get("targetHumi")
        if raw_target_humi is not None:
            dfan.target_humidity = scale_value(raw_target_humi)

    hmdf = HumidifierState()
    if "hmdf" in reported:
        hd = reported["hmdf"]
        hmdf.on = bool(hd.get("on", 0))
        hmdf.level = hd.get("lv", hd.get("level", 0))
        hmdf.mode = hd.get("mode", 0)
        hmdf.water_warning = bool(hd.get("waterWarn", 0))

    heater = HeaterState()
    if "heat" in reported:
        htd = reported["heat"]
        heater.on = bool(htd.get("on", 0))
        heater.level = htd.get("lv", htd.get("level", 0))
        heater.mode = htd.get("mode", 0)
        heater.state = htd.get("state", 0)

    return DeviceState(
        device_id=device_id,
        sensors=sensors,
        light=light,
        circulation_fan=cfan,
        duct_fan=dfan,
        humidifier=hmdf,
        heater=heater,
        raw_shadow=shadow,
        last_updated=datetime.now(UTC),
    )
