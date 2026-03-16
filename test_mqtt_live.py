"""Live MQTT test — fetch current shadows, then stream real-time updates.

Uses raw websockets + manual MQTT packets because AWS IoT requires
the 'mqtt' WebSocket subprotocol header.
"""

import asyncio
import json
import os
import ssl
import struct
from pathlib import Path

import websockets

from pyvivosun.auth import AuthManager
from pyvivosun.rest import RestClient
from pyvivosun.sigv4 import build_presigned_wss_url

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


# --- Minimal MQTT 3.1.1 packet helpers ---

def _encode_utf8(s: str) -> bytes:
    encoded = s.encode("utf-8")
    return struct.pack("!H", len(encoded)) + encoded


def _encode_remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        byte = length % 128
        length //= 128
        if length > 0:
            byte |= 0x80
        out.append(byte)
        if length == 0:
            break
    return bytes(out)


def build_connect(client_id: str, keepalive: int = 60) -> bytes:
    variable = _encode_utf8("MQTT") + bytes([0x04, 0x02]) + struct.pack("!H", keepalive)
    payload = _encode_utf8(client_id)
    remaining = _encode_remaining_length(len(variable) + len(payload))
    return bytes([0x10]) + remaining + variable + payload


def build_subscribe(packet_id: int, topics: list[str], qos: int = 1) -> bytes:
    variable = struct.pack("!H", packet_id)
    payload = b""
    for t in topics:
        payload += _encode_utf8(t) + bytes([qos])
    remaining = _encode_remaining_length(len(variable) + len(payload))
    return bytes([0x82]) + remaining + variable + payload


def build_publish(topic: str, payload_bytes: bytes) -> bytes:
    variable = _encode_utf8(topic)
    remaining = _encode_remaining_length(len(variable) + len(payload_bytes))
    return bytes([0x30]) + remaining + variable + payload_bytes


def build_puback(packet_id: int) -> bytes:
    return bytes([0x40, 0x02]) + struct.pack("!H", packet_id)


def build_pingreq() -> bytes:
    return bytes([0xC0, 0x00])


def parse_publish(data: bytes) -> tuple[str, bytes] | None:
    """Parse a PUBLISH packet, handling QoS 0 and QoS 1."""
    first_byte = data[0]
    if (first_byte >> 4) != 3:
        return None

    qos = (first_byte >> 1) & 0x03

    # Decode remaining length
    idx = 1
    remaining = 0
    multiplier = 1
    while True:
        byte = data[idx]
        remaining += (byte & 0x7F) * multiplier
        multiplier *= 128
        idx += 1
        if (byte & 0x80) == 0:
            break

    start = idx
    topic_len = struct.unpack("!H", data[idx:idx + 2])[0]
    idx += 2
    topic = data[idx:idx + topic_len].decode("utf-8")
    idx += topic_len

    packet_id = None
    if qos > 0:
        packet_id = struct.unpack("!H", data[idx:idx + 2])[0]
        idx += 2

    payload = data[idx:start + remaining]
    return topic, payload


def parse_packet_id_from_publish(data: bytes) -> int | None:
    """Extract packet ID from a QoS 1+ PUBLISH for PUBACK."""
    first_byte = data[0]
    qos = (first_byte >> 1) & 0x03
    if qos == 0:
        return None

    idx = 1
    while data[idx] & 0x80:
        idx += 1
    idx += 1  # past remaining length

    topic_len = struct.unpack("!H", data[idx:idx + 2])[0]
    idx += 2 + topic_len
    return struct.unpack("!H", data[idx:idx + 2])[0]


async def main() -> None:
    email, password = load_credentials()
    rest = RestClient()
    auth = AuthManager(rest, email, password)

    try:
        await auth.ensure_authenticated()
        headers = auth.get_rest_headers()
        raw_devices = await rest.get_device_list(headers)
        devices = [d for d in raw_devices if d.get("clientId")]

        print(f"Found {len(devices)} MQTT-capable device(s):\n")
        for d in devices:
            print(f"  - {d['name']}")
        print()

        # Get AWS credentials
        identity = await rest.get_aws_identity(headers)
        cognito = await rest.get_cognito_credentials(
            identity["awsIdentityId"], identity["awsOpenIdToken"]
        )
        creds = cognito["Credentials"]
        host = identity["awsHost"]
        region = identity.get("awsRegion", "us-east-2")
        port = int(identity.get("awsPort", 443))

        wss_url = build_presigned_wss_url(
            host=host, region=region,
            access_key=creds["AccessKeyId"],
            secret_key=creds["SecretKey"],
            session_token=creds["SessionToken"],
            port=port,
        )

        print("Connecting to MQTT over WebSocket...")
        ssl_context = ssl.create_default_context()

        async with websockets.connect(
            wss_url,
            ssl=ssl_context,
            subprotocols=[websockets.Subprotocol("mqtt")],
            compression=None,
            ping_interval=None,
            ping_timeout=None,
        ) as ws:
            await ws.send(build_connect("pyvivosun-live-" + os.urandom(4).hex()))
            connack = await ws.recv()
            if len(connack) < 4 or connack[3] != 0:
                print(f"CONNACK failed: {connack.hex()}")
                return
            print("Connected!\n")

            # Subscribe per-device
            pkt_id = 1
            for d in devices:
                cid = d["clientId"]
                prefix = d.get("topicPrefix", "")
                topics = [
                    f"$aws/things/{cid}/shadow/get/accepted",
                    f"$aws/things/{cid}/shadow/update/accepted",
                    f"$aws/things/{cid}/shadow/update/documents",
                ]
                if prefix:
                    topics.append(f"{prefix}/channel/app")

                await ws.send(build_subscribe(pkt_id, topics))
                pkt_id += 1
                await asyncio.wait_for(ws.recv(), timeout=5)  # SUBACK
                print(f"  Subscribed: {d['name']} ({len(topics)} topics)")

            # Request shadows
            print()
            for d in devices:
                await ws.send(build_publish(f"$aws/things/{d['clientId']}/shadow/get", b""))

            print("--- Streaming (Ctrl+C to stop) ---\n")

            # Keepalive
            async def keepalive():
                while True:
                    await asyncio.sleep(30)
                    await ws.send(build_pingreq())

            keepalive_task = asyncio.create_task(keepalive())

            try:
                async for raw in ws:
                    if isinstance(raw, str):
                        continue
                    data = bytes(raw)
                    pkt_type = data[0] >> 4

                    if pkt_type != 3:  # Not PUBLISH
                        continue

                    # Send PUBACK for QoS 1
                    pid = parse_packet_id_from_publish(data)
                    if pid is not None:
                        await ws.send(build_puback(pid))

                    parsed = parse_publish(data)
                    if not parsed:
                        continue
                    topic, payload_bytes = parsed

                    # Match device
                    name = "?"
                    for d in devices:
                        if d["clientId"] in topic or (
                            d.get("topicPrefix") and d["topicPrefix"] in topic
                        ):
                            name = d["name"]
                            break

                    # Try JSON first, then show raw hex
                    try:
                        payload = json.loads(payload_bytes)
                    except (json.JSONDecodeError, ValueError):
                        # Show raw bytes for non-JSON payloads
                        print(f"[{name}] {topic.split('/')[-1]}")
                        print(f"  raw ({len(payload_bytes)} bytes): {payload_bytes[:100]}")
                        print()
                        continue

                    if "/shadow/get/accepted" in topic:
                        reported = payload.get("state", {}).get("reported", {})
                        print(f"[{name}] SHADOW (current state):")
                        print(json.dumps(reported, indent=2))
                    elif "/channel/app" in topic:
                        print(f"[{name}] CHANNEL/APP (live sensor push):")
                        print(json.dumps(payload, indent=2))
                    elif "/shadow/update/" in topic:
                        current = payload.get("current", {}).get("state", {}).get("reported", {})
                        if current:
                            print(f"[{name}] SHADOW UPDATE:")
                            print(json.dumps(current, indent=2))
                        else:
                            print(f"[{name}] SHADOW UPDATE:")
                            print(json.dumps(payload, indent=2))
                    else:
                        print(f"[{name}] {topic}:")
                        print(json.dumps(payload, indent=2))
                    print()

            finally:
                keepalive_task.cancel()

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        await rest.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
