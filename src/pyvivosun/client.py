"""VivosunClient — high-level facade for the Vivosun GrowHub API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .auth import AuthManager
from .const import NATURAL_WIND_VALUE
from .exceptions import DeviceNotFoundError, InvalidParameterError
from .models.device import Device, DeviceType
from .models.event import EventType, VivosunEvent
from .models.state import DeviceState, SensorData, parse_shadow_to_state
from .mqtt import MqttClient
from .rest import RestClient
from .util import (
    clamp_fan_level,
    clamp_heater_level,
    clamp_humidifier_level,
    clamp_light_level,
    is_sentinel,
    scale_value,
)

_LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[VivosunEvent], None | Awaitable[None]]
Unsubscribe = Callable[[], None]


class _EventBus:
    """Internal event bus with typed callbacks and unsubscribe support."""

    def __init__(self) -> None:
        self._listeners: dict[EventType, list[EventCallback]] = {}

    def on(self, event_type: EventType, callback: EventCallback) -> Unsubscribe:
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)

        def unsub() -> None:
            self._listeners[event_type].remove(callback)

        return unsub

    async def emit(self, event: VivosunEvent) -> None:
        for cb in self._listeners.get(event.event_type, []):
            result = cb(event)
            if asyncio.iscoroutine(result):
                await result


class VivosunClient:
    """High-level async client for the Vivosun GrowHub cloud API.

    Usage::

        async with VivosunClient("email", "password") as client:
            devices = await client.get_devices()
            state = client.get_state(devices[0].device_id)
    """

    def __init__(
        self,
        email: str,
        password: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._rest = RestClient(session)
        self._auth = AuthManager(self._rest, email, password)
        self._mqtt = MqttClient(
            self._auth,
            on_shadow_update=self._on_shadow_update,
            on_connection_state=self._on_connection_state,
        )
        self._event_bus = _EventBus()
        self._devices: dict[str, Device] = {}
        self._states: dict[str, DeviceState] = {}
        # Reverse map: client_id -> device_id
        self._client_to_device: dict[str, str] = {}

    async def connect(self) -> None:
        """Authenticate, discover devices, and connect MQTT."""
        await self._auth.ensure_authenticated()
        await self._discover_devices()

        client_ids = [d.client_id for d in self._devices.values()]
        if client_ids:
            await self._mqtt.connect(client_ids)
            await self._auth.start_credential_refresh()

    async def disconnect(self) -> None:
        """Disconnect MQTT and clean up."""
        await self._mqtt.disconnect()
        await self._auth.stop()
        await self._rest.close()

    async def __aenter__(self) -> VivosunClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    # --- Discovery ---

    async def _discover_devices(self) -> None:
        headers = self._auth.get_rest_headers()
        raw_devices = await self._rest.get_device_list(headers)

        for raw in raw_devices:
            device_id = str(raw.get("deviceId", ""))
            client_id = str(raw.get("clientId", ""))
            scene = raw.get("scene", {})
            scene_id = scene.get("sceneId", 0) if isinstance(scene, dict) else 0
            online = bool(
                raw.get("onlineStatus", raw.get("online", False))
            )
            device = Device(
                device_id=device_id,
                client_id=client_id,
                name=raw.get("name", raw.get("deviceName", "Unknown")),
                device_type=self._infer_device_type(raw),
                topic_prefix=raw.get("topicPrefix", ""),
                scene_id=str(scene_id),
                online=online,
                model=raw.get("model"),
            )
            self._devices[device_id] = device
            if client_id:
                self._client_to_device[client_id] = device_id

    @staticmethod
    def _infer_device_type(raw: dict[str, Any]) -> DeviceType:
        model = str(raw.get("model", "")).lower()
        name = str(raw.get("deviceName", raw.get("name", ""))).lower()
        combined = f"{model} {name}"
        if "controller" in combined or "growhub" in combined:
            return DeviceType.CONTROLLER
        if "humidifier" in combined or "aerostream" in combined:
            return DeviceType.HUMIDIFIER
        if "heater" in combined or "aeroflux" in combined:
            return DeviceType.HEATER
        if "camera" in combined or "growcam" in combined:
            return DeviceType.CAMERA
        if "light" in combined or "led" in combined:
            return DeviceType.LIGHT
        if "circulation" in combined or "clip" in combined:
            return DeviceType.CIRCULATION_FAN
        if "duct" in combined or "inline" in combined:
            return DeviceType.DUCT_FAN
        return DeviceType.UNKNOWN

    async def get_devices(self) -> list[Device]:
        """Return all discovered devices."""
        return list(self._devices.values())

    async def get_device(self, device_id: str) -> Device | None:
        """Return a device by ID, or None if not found."""
        return self._devices.get(device_id)

    # --- State ---

    def get_state(self, device_id: str) -> DeviceState | None:
        """Return cached state for a device (sync, no I/O)."""
        return self._states.get(device_id)

    async def get_sensor_data(self, device_id: str) -> SensorData | None:
        """Fetch fresh sensor data via REST (poll).

        Extracts all available sensor fields from the latest point log entry.
        GrowHub uses inTemp/inHumi/inVpd for probe readings;
        humidifier/heater use pTemp/pHumi/pVpd.
        """
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(f"Device {device_id} not found")

        headers = self._auth.get_rest_headers()
        data = await self._rest.get_point_log(
            headers, device.device_id, int(device.scene_id)
        )
        if not data:
            return None

        latest = data[-1] if isinstance(data, list) else data
        sensors = SensorData()

        # Primary probe reading: GrowHub=inTemp, humidifier/heater=pTemp
        raw_temp = latest.get("inTemp")
        if raw_temp is None:
            raw_temp = latest.get("pTemp")
        if raw_temp is not None and not is_sentinel(raw_temp):
            sensors.temperature = scale_value(raw_temp)

        raw_humi = latest.get("inHumi")
        if raw_humi is None:
            raw_humi = latest.get("pHumi")
        if raw_humi is not None and not is_sentinel(raw_humi):
            sensors.humidity = scale_value(raw_humi)

        raw_vpd = latest.get("inVpd")
        if raw_vpd is None:
            raw_vpd = latest.get("pVpd")
        if raw_vpd is not None and not is_sentinel(raw_vpd):
            sensors.vpd = scale_value(raw_vpd)

        # Outside sensors (GrowHub controller only)
        raw = latest.get("outTemp")
        if raw is not None and not is_sentinel(raw):
            sensors.outside_temperature = scale_value(raw)
        raw = latest.get("outHumi")
        if raw is not None and not is_sentinel(raw):
            sensors.outside_humidity = scale_value(raw)
        raw = latest.get("outVpd")
        if raw is not None and not is_sentinel(raw):
            sensors.outside_vpd = scale_value(raw)

        # Device hardware
        raw = latest.get("coreTemp")
        if raw is not None and not is_sentinel(raw):
            sensors.core_temperature = scale_value(raw)
        raw = latest.get("rssi")
        if raw is not None and not is_sentinel(raw):
            sensors.rssi = int(raw)

        # Humidifier-specific
        raw = latest.get("waterLv")
        if raw is not None and not is_sentinel(raw):
            sensors.water_level = int(raw)

        return sensors

    # --- Commands ---

    async def set_light(
        self,
        device_id: str,
        *,
        on: bool | None = None,
        level: int | None = None,
        mode: int | None = None,
        spectrum: int | None = None,
    ) -> None:
        """Set light state."""
        desired: dict[str, Any] = {}
        if on is not None:
            desired["on"] = int(on)
        if level is not None:
            desired["level"] = clamp_light_level(level)
        if mode is not None:
            desired["mode"] = mode
        if spectrum is not None:
            desired["spectrum"] = spectrum

        if not desired:
            raise InvalidParameterError("At least one parameter required")
        await self._publish_desired(device_id, {"light": desired})

    async def set_circulation_fan(
        self,
        device_id: str,
        *,
        on: bool | None = None,
        level: int | None = None,
        oscillation: bool | None = None,
        night_mode: bool | None = None,
        natural_wind: bool | None = None,
    ) -> None:
        """Set circulation fan state."""
        desired: dict[str, Any] = {}
        if on is not None:
            desired["on"] = int(on)
        if natural_wind is True:
            desired["level"] = NATURAL_WIND_VALUE
        elif level is not None:
            desired["level"] = clamp_fan_level(level)
        if oscillation is not None:
            desired["oscillation"] = int(oscillation)
        if night_mode is not None:
            desired["nightMode"] = int(night_mode)

        if not desired:
            raise InvalidParameterError("At least one parameter required")
        await self._publish_desired(device_id, {"cFan": desired})

    async def set_duct_fan(
        self,
        device_id: str,
        *,
        on: bool | None = None,
        level: int | None = None,
        auto_mode: bool | None = None,
        target_temp: float | None = None,
        target_humidity: float | None = None,
    ) -> None:
        """Set duct fan state."""
        desired: dict[str, Any] = {}
        if on is not None:
            desired["on"] = int(on)
        if level is not None:
            desired["level"] = clamp_fan_level(level)
        if auto_mode is not None:
            desired["auto"] = int(auto_mode)
        if target_temp is not None:
            desired["targetTemp"] = int(target_temp * 100)
        if target_humidity is not None:
            desired["targetHumi"] = int(target_humidity * 100)

        if not desired:
            raise InvalidParameterError("At least one parameter required")
        await self._publish_desired(device_id, {"dFan": desired})

    async def set_humidifier(
        self,
        device_id: str,
        *,
        on: bool | None = None,
        level: int | None = None,
        mode: int | None = None,
        target_humidity: float | None = None,
    ) -> None:
        """Set humidifier state.

        Args:
            on: Turn on/off.
            level: Manual level (0-10). Sets mode to manual (0).
            mode: 0=manual, 1=auto.
            target_humidity: Auto mode target humidity (0-100).
        """
        desired: dict[str, Any] = {}
        if on is not None:
            desired["on"] = int(on)
        if level is not None:
            desired["mode"] = 0
            desired["manu"] = {"lv": clamp_humidifier_level(level)}
        if mode is not None:
            desired["mode"] = mode
        if target_humidity is not None:
            desired["targetHumi"] = int(target_humidity * 100)

        if not desired:
            raise InvalidParameterError("At least one parameter required")
        await self._publish_desired(device_id, {"hmdf": desired})

    async def set_heater(
        self,
        device_id: str,
        *,
        on: bool | None = None,
        level: int | None = None,
        mode: int | None = None,
        target_temp: float | None = None,
    ) -> None:
        """Set heater state.

        Args:
            on: Turn on/off.
            level: Manual level (0-10). Sets mode to manual (0).
            mode: 0=manual, 1=auto.
            target_temp: Auto mode target temperature (Celsius).
        """
        desired: dict[str, Any] = {}
        if on is not None:
            desired["on"] = int(on)
        if level is not None:
            desired["mode"] = 0
            desired["manu"] = {"lv": clamp_heater_level(level)}
        if mode is not None:
            desired["mode"] = mode
        if target_temp is not None:
            desired["targetTemp"] = int(target_temp * 100)

        if not desired:
            raise InvalidParameterError("At least one parameter required")
        await self._publish_desired(device_id, {"heat": desired})

    async def _publish_desired(
        self, device_id: str, desired: dict[str, Any]
    ) -> None:
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(f"Device {device_id} not found")
        await self._mqtt.publish_shadow_update(device.client_id, desired)

    # --- Events ---

    def on_state_changed(self, callback: EventCallback) -> Unsubscribe:
        """Register callback for state changes. Returns unsubscribe callable."""
        return self._event_bus.on(EventType.STATE_CHANGED, callback)

    def on_device_online(self, callback: EventCallback) -> Unsubscribe:
        """Register callback for device online/offline events."""
        return self._event_bus.on(EventType.DEVICE_ONLINE, callback)

    def on_connection_changed(self, callback: EventCallback) -> Unsubscribe:
        """Register callback for MQTT connection state changes."""
        return self._event_bus.on(EventType.CONNECTION_CHANGED, callback)

    # --- Internal callbacks ---

    async def _on_shadow_update(
        self, client_id: str, payload: dict[str, Any]
    ) -> None:
        device_id = self._client_to_device.get(client_id)
        if device_id is None:
            return

        # Merge delta into existing state
        existing = self._states.get(device_id)
        new_state = parse_shadow_to_state(device_id, payload)

        if existing is not None:
            # Merge: keep existing sensor values (sensors come from REST, not MQTT)
            for attr in (
                "temperature", "humidity", "vpd",
                "outside_temperature", "outside_humidity", "outside_vpd",
                "core_temperature", "rssi", "water_level",
            ):
                if getattr(new_state.sensors, attr) is None:
                    setattr(
                        new_state.sensors, attr, getattr(existing.sensors, attr)
                    )

        self._states[device_id] = new_state

        await self._event_bus.emit(
            VivosunEvent(
                event_type=EventType.STATE_CHANGED,
                device_id=device_id,
                data=new_state,
            )
        )

    async def _on_connection_state(self, connected: bool) -> None:
        await self._event_bus.emit(
            VivosunEvent(
                event_type=EventType.CONNECTION_CHANGED,
                data=connected,
            )
        )
