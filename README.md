# pyvivosun

Async Python library for the Vivosun GrowHub cloud API. Provides programmatic access to sensor data and device control for Vivosun grow tent equipment.

Built on the reverse-engineering work by [lientry](https://github.com/lientry/homeassistant-vivosun-growhub), who figured out the authentication flow, API endpoints, MQTT shadow structure, and device control protocol. This library is a clean-room standalone rewrite with no Home Assistant dependencies.

## Supported Devices

Tested with the following Vivosun devices (each connects independently to the cloud via WiFi):

| Device | Type | Sensors | Controls |
|--------|------|---------|----------|
| GrowHub E42A | Controller | Inside/outside temp, humidity, VPD, core temp, RSSI | Light, circulation fan, duct fan |
| AeroStream H19 | Humidifier | Probe temp, humidity, VPD, water level, core temp | Humidifier level/mode |
| AeroFlux W70 | Heater | Probe temp, humidity, VPD | Heater level/mode |
| GrowCam C4 | Camera | — | — (no MQTT, video only) |

## Installation

```bash
pip install pyvivosun
```

Or install from source:

```bash
git clone https://github.com/hottex888/pyvivosun.git
cd pyvivosun
pip install -r requirements.txt
pip install -e .
```

For development (tests, linting, type checking):

```bash
pip install -e ".[dev]"
```

Requires Python 3.11+. Dependencies: `aiohttp`, `websockets`.

## Testing with Your Devices

The repo includes live test scripts you can run against your own Vivosun account. Create a `credentials.env` file (gitignored) in the project root:

```
VIVOSUN_EMAIL=your@email.com
VIVOSUN_PASSWORD=yourpassword
```

Then run:

```bash
# REST: login, list devices, fetch sensor data
python test_live.py

# MQTT: connect, subscribe to shadows, stream live updates
python test_mqtt_live.py

# MQTT: debug per-topic subscription
python test_mqtt_debug.py

# REST: test different time ranges and aggregation levels
python test_pointlog_ranges.py
```

## Quick Start

```python
import asyncio
from pyvivosun import VivosunClient

async def main():
    async with VivosunClient("email@example.com", "password") as client:
        # List all devices
        devices = await client.get_devices()
        for d in devices:
            print(f"{d.name} (online={d.online})")

        # Read cached control state (from MQTT shadow, no I/O)
        state = client.get_state(devices[0].device_id)
        if state:
            print(f"Light on={state.light.on}, level={state.light.level}")

        # Poll fresh sensor data via REST (the only source of temp/humidity/VPD)
        sensors = await client.get_sensor_data(devices[0].device_id)
        if sensors:
            print(f"Temp: {sensors.temperature}°C, Humidity: {sensors.humidity}%")

        # Control a light
        await client.set_light(devices[0].device_id, on=True, level=75)

        # Listen for control state changes (shadow updates)
        def on_change(event):
            print(f"Device {event.device_id} updated: {event.data}")

        unsub = client.on_state_changed(on_change)
        await asyncio.sleep(60)  # listen for 1 minute
        unsub()  # stop listening

asyncio.run(main())
```

## Architecture

### Data Flow

```
Device (WiFi) ──> AWS IoT Core ──> Shadow (control state only)
                       │
                 Vivosun Cloud ──> REST API (sensor data + control state)
                       │
                  Mobile App / pyvivosun
```

**REST — Point Log (sensor data, polling)**
- `POST /iot/data/getPointLog` returns time-series sensor data (temp, humidity, VPD)
- This is the **only** source of sensor readings — MQTT does not carry sensor data
- Configurable time range (hour, day, week, month) and aggregation granularity
- Works for all sensor-equipped devices

**MQTT — Device Shadows (control state, push)**
- AWS IoT Core device shadows contain **control state only** (light level, fan speed, heater mode, etc.)
- Shadows do **not** contain sensor readings (temperature, humidity, VPD)
- The only way to send commands to devices (publish desired state)
- Push-based updates when control state changes (e.g., fan level adjusting in auto mode)
- Requires SigV4-signed WebSocket connection with `mqtt` subprotocol

**MQTT — `channel/app` topic**
- Sends empty heartbeat payloads: `{"msgId": 0, "data": {}, "msgType": 1}`
- No sensor data observed even with the mobile app actively open
- Purpose unclear; not useful for integration

### Why Not aiomqtt?

AWS IoT Core requires the `mqtt` WebSocket subprotocol header in the upgrade request. Neither aiomqtt nor paho-mqtt support setting custom WebSocket subprotocols. This library uses the `websockets` library directly with manual MQTT 3.1.1 packet construction (~100 lines), which works reliably.

### Authentication Flow

```
1. POST /user/login ──> accessToken, loginToken, refreshToken
2. POST /iot/user/awsIdentity ──> awsIdentityId, awsOpenIdToken, awsHost
3. POST Cognito GetCredentialsForIdentity ──> AccessKeyId, SecretKey, SessionToken
4. SigV4-sign WebSocket URL ──> wss:// connection to AWS IoT
```

- REST tokens (step 1) last ~3 months
- AWS credentials (step 3) expire ~1 hour; the library auto-refreshes them every 45 minutes

### Device Connectivity

Each device maintains its own WiFi connection to the cloud — they do **not** proxy through the GrowHub. This means:
- Each device has its own `clientId` and MQTT shadow
- Devices work independently (a humidifier keeps reporting even if the GrowHub is offline)
- The GrowHub is only "special" in that it also has outside sensors and manages light/fan/duct peripherals

## REST API Reference

### Base URL

```
https://api-prod.next.vivosun.com
```

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/user/login` | No | Authenticate with email/password |
| GET | `/iot/device/getTotalList` | Yes | List all devices |
| POST | `/iot/user/awsIdentity` | Yes | Get AWS IoT Cognito identity |
| POST | `/iot/data/getPointLog` | Yes | Fetch historical sensor data |

Auth headers on protected endpoints: `login-token`, `access-token`

### Point Log — Time Levels

The `timeLevel` parameter controls aggregation granularity:

| `timeLevel` | Resolution | Recommended Range | Approx Entries |
|---|---|---|---|
| `ONE_MINUTE` | 1 point/min | Last hour | ~60 |
| `FIVE_MINUTES` | 1 point/5min | Last 24 hours | ~288 |
| `FIFTEEN_MINUTES` | 1 point/15min | Last 7 days | ~672 |
| `THIRTY_MINUTES` | 1 point/30min | Last 7 days | ~336 |
| `ONE_HOUR` | 1 point/hour | Last 30 days | ~720 |

The API does not enforce a maximum entry count — requesting `ONE_MINUTE` over 30 days returns ~43,000 entries. Use coarser time levels for longer ranges.

### Point Log — Sensor Fields by Device

**GrowHub E42A** (controller):
| Field | Description | Scale |
|-------|-------------|-------|
| `inTemp` | Inside temperature | /100 (°C) |
| `inHumi` | Inside humidity | /100 (%) |
| `inVpd` | Inside VPD | /100 (kPa) |
| `outTemp` | Outside temperature | /100 (°C) |
| `outHumi` | Outside humidity | /100 (%) |
| `outVpd` | Outside VPD | /100 (kPa) |
| `coreTemp` | Core/board temperature | /100 (°C) |
| `rssi` | WiFi signal strength | raw (dBm) |
| `light.lv` | Light level | 0-100 |
| `light.mode` | Light mode | 0=manual |
| `cFan.lv` | Circulation fan level | 0-100 |
| `dFan.lv` | Duct fan level | 0-100 |

**AeroStream H19** (humidifier):
| Field | Description | Scale |
|-------|-------------|-------|
| `pTemp` | Probe temperature | /100 (°C) |
| `pHumi` | Probe humidity | /100 (%) |
| `pVpd` | Probe VPD | /100 (kPa) |
| `waterLv` | Water level | /100 (%) |
| `coreTemp` | Core temperature | /100 (°C) |
| `hmdf.lv` | Humidifier level | 0-100 |
| `hmdf.mode` | Humidifier mode | 0=manual |
| `hmdf.waterWarn` | Water warning | 0/1 |

**AeroFlux W70** (heater):
| Field | Description | Scale |
|-------|-------------|-------|
| `pTemp` | Probe temperature | /100 (°C) |
| `pHumi` | Probe humidity | /100 (%) |
| `pVpd` | Probe VPD | /100 (kPa) |
| `heat.lv` | Heater level | 0-100 |
| `heat.mode` | Heater mode | 0=manual |
| `heat.state` | Heater state | 0/1 |

**Sentinel value**: `-6666` means the sensor reading is unavailable.

**All temperature/humidity/VPD values** are raw integers divided by 100 (e.g., `2361` = 23.61°C).

## MQTT Shadow Fields

Shadows contain **control state only** (no sensor data). Fields per device type:

**GrowHub E42A**: `light.{on, lv, mode, spectrum}`, `cFan.{on, lv, oscillation, nightMode}`, `dFan.{on, lv, auto, targetTemp, targetHumi}`

**AeroStream H19**: `hmdf.{on, lv, mode, waterWarn}`

**AeroFlux W70**: `heat.{on, lv, mode, state}`

### MQTT Topics

For each device with a `clientId`:

```
$aws/things/{clientId}/shadow/get           # Request current state
$aws/things/{clientId}/shadow/get/accepted  # State response
$aws/things/{clientId}/shadow/update        # Send commands (desired state)
$aws/things/{clientId}/shadow/update/accepted
$aws/things/{clientId}/shadow/update/delta  # Desired vs reported diff
{topicPrefix}/channel/app                   # Heartbeat only (empty data)
```

### SigV4 Quirk

The MQTT WebSocket connection uses SigV4 presigned URLs. The session token must be **excluded** from the canonical query string during signing but **appended** to the final URL afterward. This is an AWS IoT-specific requirement.

### WebSocket Subprotocol Quirk

AWS IoT requires the `mqtt` WebSocket subprotocol header in the HTTP upgrade request. Standard MQTT libraries (paho-mqtt, aiomqtt) do not set this, causing silent connection failures. This library uses raw `websockets` with manual MQTT 3.1.1 packet construction to work around this.

## Library API

### VivosunClient

```python
VivosunClient(email, password, *, session=None)
```

- `session`: Optional `aiohttp.ClientSession` for HA integration session sharing

**Lifecycle:**
- `await client.connect()` / `await client.disconnect()`
- Async context manager: `async with VivosunClient(...) as client:`

**Discovery:**
- `await client.get_devices() -> list[Device]`
- `await client.get_device(device_id) -> Device | None`

**State:**
- `client.get_state(device_id) -> DeviceState | None` — sync, reads in-memory shadow cache (control state only)
- `await client.get_sensor_data(device_id) -> SensorData | None` — REST poll (temp, humidity, VPD)

**Commands:**
- `await client.set_light(device_id, *, on, level, mode, spectrum)`
- `await client.set_circulation_fan(device_id, *, on, level, oscillation, night_mode, natural_wind)`
- `await client.set_duct_fan(device_id, *, on, level, auto_mode, target_temp, target_humidity)`

**Events (callback with unsubscribe):**
- `client.on_state_changed(callback) -> unsub()` — control state changes from MQTT shadows
- `client.on_device_online(callback) -> unsub()`
- `client.on_connection_changed(callback) -> unsub()`

### Constants

```python
from pyvivosun.const import (
    TIME_LEVEL_ONE_MINUTE,
    TIME_LEVEL_FIVE_MINUTES,
    TIME_LEVEL_FIFTEEN_MINUTES,
    TIME_LEVEL_THIRTY_MINUTES,
    TIME_LEVEL_ONE_HOUR,
)
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Type check
mypy src/pyvivosun/ --strict

# Lint
ruff check src/ tests/
```
