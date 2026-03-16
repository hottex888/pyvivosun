"""Debug MQTT connection — minimal subscribe to isolate the issue."""

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


async def main() -> None:
    email, password = load_credentials()
    rest = RestClient()
    auth = AuthManager(rest, email, password)

    try:
        await auth.ensure_authenticated()
        headers = auth.get_rest_headers()
        raw_devices = await rest.get_device_list(headers)
        devices = [d for d in raw_devices if d.get("clientId")]

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

        ssl_context = ssl.create_default_context()

        print("Connecting...")
        async with websockets.connect(
            wss_url,
            ssl=ssl_context,
            subprotocols=[websockets.Subprotocol("mqtt")],
            compression=None,
            ping_interval=None,
            ping_timeout=None,
        ) as ws:
            # CONNECT
            await ws.send(build_connect("pyvivosun-debug"))
            connack = await ws.recv()
            rc = connack[3] if len(connack) >= 4 else -1
            print(f"CONNACK: rc={rc} ({'OK' if rc == 0 else 'FAIL'})")
            if rc != 0:
                return

            # Try subscribing to ONE topic at a time
            d = devices[0]
            cid = d["clientId"]
            prefix = d.get("topicPrefix", "")

            test_topics = [
                f"$aws/things/{cid}/shadow/get/accepted",
                f"$aws/things/{cid}/shadow/update/accepted",
                f"{prefix}/channel/app",
            ]

            for i, topic in enumerate(test_topics, 1):
                print(f"\nSubscribing to: {topic}")
                try:
                    await ws.send(build_subscribe(i, [topic]))
                    resp = await asyncio.wait_for(ws.recv(), timeout=5)
                    pkt_type = resp[0] >> 4
                    if pkt_type == 9:  # SUBACK
                        return_code = resp[4] if len(resp) >= 5 else -1
                        status = "OK" if return_code <= 2 else f"FAILED (rc={return_code})"
                        print(f"  SUBACK: {status}")
                    else:
                        print(f"  Got packet type {pkt_type} instead of SUBACK: {resp[:20].hex()}")
                except asyncio.TimeoutError:
                    print("  Timeout waiting for SUBACK")
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"  Connection closed: {e}")
                    return

            # If we got here, try requesting shadow
            print(f"\nPublishing shadow/get for {d['name']}...")
            await ws.send(build_publish(f"$aws/things/{cid}/shadow/get", b""))

            print("Waiting for messages (10s)...\n")
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    if isinstance(raw, str):
                        continue
                    data = bytes(raw)
                    pkt_type = data[0] >> 4
                    if pkt_type == 3:  # PUBLISH
                        remaining, idx = 0, 1
                        multiplier = 1
                        while True:
                            byte = data[idx]
                            remaining += (byte & 0x7F) * multiplier
                            multiplier *= 128
                            idx += 1
                            if (byte & 0x80) == 0:
                                break
                        topic_len = struct.unpack("!H", data[idx:idx+2])[0]
                        idx += 2
                        topic = data[idx:idx+topic_len].decode("utf-8")
                        idx += topic_len
                        payload = data[idx:]
                        try:
                            obj = json.loads(payload)
                            if "/shadow/get/accepted" in topic:
                                reported = obj.get("state", {}).get("reported", {})
                                print(f"SHADOW [{d['name']}]:")
                                print(json.dumps(reported, indent=2))
                            elif "/channel/app" in topic:
                                print(f"CHANNEL [{d['name']}]:")
                                print(json.dumps(obj, indent=2))
                            else:
                                print(f"[{topic}]:")
                                print(json.dumps(obj, indent=2))
                        except Exception:
                            print(f"[{topic}]: {len(payload)} bytes")
                        print()
                    else:
                        print(f"(packet type {pkt_type})")
            except asyncio.TimeoutError:
                print("No more messages.")

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        await rest.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
