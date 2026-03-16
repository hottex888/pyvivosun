"""MQTT client for AWS IoT Core shadow interactions.

Uses raw websockets with manual MQTT 3.1.1 packet construction because
AWS IoT requires the 'mqtt' WebSocket subprotocol header, which
aiomqtt/paho-mqtt cannot set.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import ssl
import struct
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from .auth import AuthManager
from .const import (
    SHADOW_GET,
    SHADOW_GET_ACCEPTED,
    SHADOW_UPDATE,
    SHADOW_UPDATE_ACCEPTED,
    SHADOW_UPDATE_DELTA,
)
from .sigv4 import build_presigned_wss_url

_LOGGER = logging.getLogger(__name__)

# Reconnection backoff
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0
_BACKOFF_FACTOR = 2.0

# MQTT keepalive
_KEEPALIVE_SECONDS = 60
_PING_INTERVAL = 30

ShadowCallback = Callable[[str, dict[str, Any]], None | Awaitable[None]]
ConnectionCallback = Callable[[bool], None | Awaitable[None]]


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


def _decode_remaining_length(data: bytes, start: int) -> tuple[int, int]:
    """Decode MQTT remaining length. Returns (length, next_index)."""
    idx = start
    remaining = 0
    multiplier = 1
    while True:
        byte = data[idx]
        remaining += (byte & 0x7F) * multiplier
        multiplier *= 128
        idx += 1
        if (byte & 0x80) == 0:
            break
    return remaining, idx


def _build_connect(client_id: str, keepalive: int = _KEEPALIVE_SECONDS) -> bytes:
    variable = _encode_utf8("MQTT") + bytes([0x04, 0x02]) + struct.pack("!H", keepalive)
    payload = _encode_utf8(client_id)
    remaining = _encode_remaining_length(len(variable) + len(payload))
    return bytes([0x10]) + remaining + variable + payload


def _build_subscribe(packet_id: int, topics: list[str], qos: int = 1) -> bytes:
    variable = struct.pack("!H", packet_id)
    payload = b""
    for t in topics:
        payload += _encode_utf8(t) + bytes([qos])
    remaining = _encode_remaining_length(len(variable) + len(payload))
    return bytes([0x82]) + remaining + variable + payload


def _build_publish(topic: str, payload_bytes: bytes, qos: int = 0) -> bytes:
    variable = _encode_utf8(topic)
    remaining = _encode_remaining_length(len(variable) + len(payload_bytes))
    return bytes([0x30]) + remaining + variable + payload_bytes


def _build_puback(packet_id: int) -> bytes:
    return bytes([0x40, 0x02]) + struct.pack("!H", packet_id)


def _build_pingreq() -> bytes:
    return bytes([0xC0, 0x00])


def _build_disconnect() -> bytes:
    return bytes([0xE0, 0x00])


def _parse_publish(data: bytes) -> tuple[str, bytes, int | None] | None:
    """Parse a PUBLISH packet. Returns (topic, payload, packet_id) or None."""
    first_byte = data[0]
    if (first_byte >> 4) != 3:
        return None

    qos = (first_byte >> 1) & 0x03
    remaining, idx = _decode_remaining_length(data, 1)
    start = idx

    topic_len = struct.unpack("!H", data[idx : idx + 2])[0]
    idx += 2
    topic = data[idx : idx + topic_len].decode("utf-8")
    idx += topic_len

    packet_id = None
    if qos > 0:
        packet_id = struct.unpack("!H", data[idx : idx + 2])[0]
        idx += 2

    payload = data[idx : start + remaining]
    return topic, payload, packet_id


class MqttClient:
    """Async MQTT client for AWS IoT device shadows over WebSocket."""

    def __init__(
        self,
        auth: AuthManager,
        on_shadow_update: ShadowCallback | None = None,
        on_connection_state: ConnectionCallback | None = None,
    ) -> None:
        self._auth = auth
        self._on_shadow_update = on_shadow_update
        self._on_connection_state = on_connection_state
        self._ws: Any = None  # websockets connection
        self._listen_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._device_client_ids: list[str] = []
        self._connected = False
        self._should_reconnect = True

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, device_client_ids: list[str]) -> None:
        """Connect to AWS IoT and subscribe to shadow topics for all devices."""
        self._device_client_ids = device_client_ids
        self._should_reconnect = True
        await self._connect_and_subscribe()

    async def _connect_and_subscribe(self) -> None:
        creds = await self._auth.get_aws_credentials()
        wss_url = build_presigned_wss_url(
            host=creds.host,
            region=creds.region,
            access_key=creds.access_key_id,
            secret_key=creds.secret_access_key,
            session_token=creds.session_token,
            port=creds.port,
        )

        ssl_context = ssl.create_default_context()
        self._ws = await websockets.connect(
            wss_url,
            ssl=ssl_context,
            subprotocols=[websockets.Subprotocol("mqtt")],
            compression=None,
            ping_interval=None,
            ping_timeout=None,
        )

        # MQTT CONNECT
        client_id = "pyvivosun-" + os.urandom(4).hex()
        await self._ws.send(_build_connect(client_id))
        connack = await self._ws.recv()
        connack_bytes = bytes(connack) if not isinstance(connack, bytes) else connack
        if len(connack_bytes) < 4 or connack_bytes[3] != 0:
            await self._ws.close()
            rc = connack_bytes[3] if len(connack_bytes) >= 4 else -1
            raise ConnectionError(f"MQTT CONNACK failed: rc={rc}")

        self._connected = True
        await self._notify_connection(True)

        # Subscribe per-device (bulk subscribe can cause AWS IoT to disconnect)
        pkt_id = 1
        for cid in self._device_client_ids:
            topics = [
                SHADOW_GET_ACCEPTED.format(client_id=cid),
                SHADOW_UPDATE_ACCEPTED.format(client_id=cid),
                SHADOW_UPDATE_DELTA.format(client_id=cid),
            ]
            await self._ws.send(_build_subscribe(pkt_id, topics))
            pkt_id += 1
            # Wait for SUBACK
            try:
                await asyncio.wait_for(self._ws.recv(), timeout=5)
            except TimeoutError:
                _LOGGER.warning("Timeout waiting for SUBACK for %s", cid)
            _LOGGER.debug("Subscribed to shadow topics for %s", cid)

        # Request initial shadow state
        for cid in self._device_client_ids:
            topic = SHADOW_GET.format(client_id=cid)
            await self._ws.send(_build_publish(topic, b""))

        # Start listening and keepalive
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            try:
                if self._ws is not None:
                    await self._ws.send(_build_pingreq())
            except Exception:
                break

    async def _listen_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, str):
                    continue
                data = bytes(raw)
                pkt_type = data[0] >> 4

                if pkt_type != 3:  # Not PUBLISH
                    continue

                parsed = _parse_publish(data)
                if not parsed:
                    continue

                topic, payload_bytes, packet_id = parsed

                # Send PUBACK for QoS 1
                if packet_id is not None:
                    await self._ws.send(_build_puback(packet_id))

                await self._handle_message(topic, payload_bytes)

        except websockets.exceptions.ConnectionClosed as exc:
            _LOGGER.warning("MQTT WebSocket closed: %s", exc)
        except Exception:
            _LOGGER.exception("MQTT listen error")
        finally:
            self._connected = False
            await self._notify_connection(False)
            if self._should_reconnect:
                await self._reconnect_loop()

    async def _reconnect_loop(self) -> None:
        backoff = _INITIAL_BACKOFF
        while self._should_reconnect:
            _LOGGER.info("Reconnecting in %.1fs...", backoff)
            await asyncio.sleep(backoff)
            try:
                await self._connect_and_subscribe()
                _LOGGER.info("Reconnected to MQTT")
                return
            except Exception:
                _LOGGER.exception("Reconnection failed")
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

    async def _handle_message(self, topic: str, payload_bytes: bytes) -> None:
        try:
            payload = json.loads(payload_bytes)
        except (json.JSONDecodeError, ValueError):
            _LOGGER.debug(
                "Non-JSON MQTT message on %s (%d bytes)", topic, len(payload_bytes)
            )
            return

        # Extract client_id from topic: $aws/things/{client_id}/shadow/...
        parts = topic.split("/")
        if len(parts) >= 3:
            client_id = parts[2]
        else:
            return

        if self._on_shadow_update:
            result = self._on_shadow_update(client_id, payload)
            if asyncio.iscoroutine(result):
                await result

    async def publish_shadow_update(
        self, thing_name: str, desired: dict[str, Any]
    ) -> None:
        """Publish a desired state update to a device shadow."""
        if self._ws is None or not self._connected:
            raise RuntimeError("MQTT client not connected")

        topic = SHADOW_UPDATE.format(client_id=thing_name)
        payload = json.dumps({"state": {"desired": desired}}).encode("utf-8")
        await self._ws.send(_build_publish(topic, payload))

    async def disconnect(self) -> None:
        """Disconnect and stop listening."""
        self._should_reconnect = False

        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
            self._keepalive_task = None

        if self._listen_task is not None:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None

        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(_build_disconnect())
                await self._ws.close()
            self._ws = None

        self._connected = False

    async def _notify_connection(self, connected: bool) -> None:
        if self._on_connection_state:
            result = self._on_connection_state(connected)
            if asyncio.iscoroutine(result):
                await result
