"""Live test script — login and list all devices with raw API data."""

import asyncio
import json
import os
from pathlib import Path

from pyvivosun.rest import RestClient
from pyvivosun.auth import AuthManager

CREDENTIALS_FILE = Path(__file__).parent / "credentials.env"


def load_credentials() -> tuple[str, str]:
    """Load credentials from credentials.env, fall back to interactive prompt."""
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

    if not email or not password:
        import getpass
        email = email or input("Email: ")
        password = password or getpass.getpass("Password: ")

    return email, password


async def main() -> None:
    email, password = load_credentials()

    rest = RestClient()
    auth = AuthManager(rest, email, password)

    try:
        print("\n--- Logging in...")
        await auth.ensure_authenticated()
        assert auth.tokens is not None
        print(f"OK — user_id: {auth.tokens.user_id}")

        print("\n--- Fetching device list...")
        headers = auth.get_rest_headers()
        raw_devices = await rest.get_device_list(headers)
        print(f"Found {len(raw_devices)} device(s):\n")

        for i, dev in enumerate(raw_devices, 1):
            print(f"=== Device {i} ===")
            print(json.dumps(dev, indent=2, default=str))
            print()

        print("\n--- Fetching AWS IoT identity...")
        identity_data = await rest.get_aws_identity(headers)
        print(f"Host: {identity_data.get('awsHost')}")
        print(f"Region: {identity_data.get('awsRegion')}")

        # Try fetching point log for each device
        for dev in raw_devices:
            device_id = str(dev.get("deviceId", ""))
            scene = dev.get("scene", {})
            scene_id = scene.get("sceneId") if isinstance(scene, dict) else None
            name = dev.get("name", "Unknown")

            if not scene_id:
                print(f"\n--- Skipping point log for '{name}' (no sceneId)")
                continue

            print(f"\n--- Point log for '{name}' ({device_id})...")
            try:
                points = await rest.get_point_log(
                    headers, device_id, int(scene_id)
                )
                if points:
                    latest = points[-1]
                    print(f"  {len(points)} data point(s), latest entry keys:")
                    print(f"  {sorted(latest.keys())}")
                    print(f"  Raw latest: {json.dumps(latest, indent=4, default=str)}")
                else:
                    print("  (no data returned)")
            except Exception as e:
                print(f"  Error: {e}")

    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
