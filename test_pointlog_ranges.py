"""Test different point log time ranges and timeLevel values."""

import asyncio
import json
import os
import time
from pathlib import Path

from pyvivosun.rest import RestClient
from pyvivosun.auth import AuthManager

CREDENTIALS_FILE = Path(__file__).parent / "credentials.env"


def load_credentials() -> tuple[str, str]:
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
    return email, password


# Time ranges to test
NOW = int(time.time())
HOUR = 3600
DAY = 86400
WEEK = 7 * DAY
MONTH = 30 * DAY

RANGES = {
    "1 hour": NOW - HOUR,
    "24 hours": NOW - DAY,
    "7 days": NOW - WEEK,
    "30 days": NOW - MONTH,
}

# timeLevel values to try
TIME_LEVELS = [
    "ONE_MINUTE",
    "FIVE_MINUTES",
    "FIFTEEN_MINUTES",
    "THIRTY_MINUTES",
    "ONE_HOUR",
    "ONE_DAY",
    # Some guesses for other possible values
    "FIVE_MINUTE",
    "FIFTEEN_MINUTE",
    "THIRTY_MINUTE",
    "5m",
    "15m",
    "1h",
    "1d",
]


async def main() -> None:
    email, password = load_credentials()
    rest = RestClient()
    auth = AuthManager(rest, email, password)

    try:
        await auth.ensure_authenticated()
        headers = auth.get_rest_headers()

        # Use GrowHub as test device
        device_id = "157670637026179654"
        scene_id = 108080

        # First: test which timeLevel values work with a 1-hour range
        print("=== Testing timeLevel values (1 hour range) ===\n")
        for level in TIME_LEVELS:
            try:
                points = await rest.get_point_log(
                    headers, device_id, scene_id,
                    start_time=NOW - HOUR, end_time=NOW,
                )
                # Hack: call _request directly to pass custom timeLevel
                import aiohttp
                session = await rest._ensure_session()
                url = "https://api-prod.next.vivosun.com/iot/data/getPointLog"
                timeout = aiohttp.ClientTimeout(total=15)
                body = {
                    "deviceId": device_id,
                    "sceneId": scene_id,
                    "startTime": NOW - HOUR,
                    "endTime": NOW,
                    "reportType": 0,
                    "orderBy": "asc",
                    "timeLevel": level,
                }
                async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
                    data = await resp.json()

                if data.get("success"):
                    inner = data.get("data", {})
                    entries = inner.get("iotDataLogList", [])
                    print(f"  {level:20s} -> {len(entries):4d} entries  OK")
                else:
                    print(f"  {level:20s} -> FAILED: {data.get('message', '?')}")
            except Exception as e:
                print(f"  {level:20s} -> ERROR: {e}")

        # Second: test different ranges with working timeLevels
        print("\n=== Testing time ranges with ONE_MINUTE ===\n")
        for range_name, start in RANGES.items():
            try:
                session = await rest._ensure_session()
                url = "https://api-prod.next.vivosun.com/iot/data/getPointLog"
                timeout = aiohttp.ClientTimeout(total=15)
                body = {
                    "deviceId": device_id,
                    "sceneId": scene_id,
                    "startTime": start,
                    "endTime": NOW,
                    "reportType": 0,
                    "orderBy": "asc",
                    "timeLevel": "ONE_MINUTE",
                }
                async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
                    data = await resp.json()

                if data.get("success"):
                    entries = data.get("data", {}).get("iotDataLogList", [])
                    first_t = entries[0]["time"] if entries else "n/a"
                    last_t = entries[-1]["time"] if entries else "n/a"
                    print(f"  {range_name:10s} ONE_MINUTE     -> {len(entries):5d} entries  (first: {first_t}, last: {last_t})")
                else:
                    print(f"  {range_name:10s} ONE_MINUTE     -> FAILED: {data.get('message')}")
            except Exception as e:
                print(f"  {range_name:10s} ONE_MINUTE     -> ERROR: {e}")

        # Third: test ranges with coarser levels
        print("\n=== Testing time ranges with coarser timeLevels ===\n")
        combos = [
            ("24 hours", NOW - DAY, "FIVE_MINUTES"),
            ("24 hours", NOW - DAY, "FIFTEEN_MINUTES"),
            ("24 hours", NOW - DAY, "ONE_HOUR"),
            ("7 days", NOW - WEEK, "ONE_HOUR"),
            ("7 days", NOW - WEEK, "ONE_DAY"),
            ("30 days", NOW - MONTH, "ONE_HOUR"),
            ("30 days", NOW - MONTH, "ONE_DAY"),
        ]
        for range_name, start, level in combos:
            try:
                session = await rest._ensure_session()
                url = "https://api-prod.next.vivosun.com/iot/data/getPointLog"
                timeout = aiohttp.ClientTimeout(total=15)
                body = {
                    "deviceId": device_id,
                    "sceneId": scene_id,
                    "startTime": start,
                    "endTime": NOW,
                    "reportType": 0,
                    "orderBy": "asc",
                    "timeLevel": level,
                }
                async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
                    data = await resp.json()

                if data.get("success"):
                    entries = data.get("data", {}).get("iotDataLogList", [])
                    print(f"  {range_name:10s} {level:20s} -> {len(entries):5d} entries")
                else:
                    print(f"  {range_name:10s} {level:20s} -> FAILED: {data.get('message')}")
            except Exception as e:
                print(f"  {range_name:10s} {level:20s} -> ERROR: {e}")

    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
