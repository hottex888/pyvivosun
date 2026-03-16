"""Tests for MQTT client (websockets-based)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyvivosun.models.auth import AwsCredentials
from pyvivosun.mqtt import (
    MqttClient,
    _build_connect,
    _build_disconnect,
    _build_pingreq,
    _build_puback,
    _build_publish,
    _build_subscribe,
    _parse_publish,
)


@pytest.fixture
def mock_auth() -> AsyncMock:
    auth = AsyncMock()
    auth.get_aws_credentials.return_value = AwsCredentials(
        host="iot.example.com",
        region="us-east-1",
        access_key_id="AKIA",
        secret_access_key="secret",
        session_token="token",
        port=443,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return auth


class TestMqttPackets:
    def test_build_connect(self) -> None:
        pkt = _build_connect("test-client")
        assert pkt[0] == 0x10  # CONNECT packet type

    def test_build_subscribe(self) -> None:
        pkt = _build_subscribe(1, ["topic/a", "topic/b"])
        assert pkt[0] == 0x82  # SUBSCRIBE packet type

    def test_build_publish_qos0(self) -> None:
        pkt = _build_publish("my/topic", b"hello")
        assert pkt[0] == 0x30  # PUBLISH QoS 0

    def test_build_puback(self) -> None:
        pkt = _build_puback(42)
        assert pkt[0] == 0x40
        assert len(pkt) == 4

    def test_build_pingreq(self) -> None:
        pkt = _build_pingreq()
        assert pkt == bytes([0xC0, 0x00])

    def test_build_disconnect(self) -> None:
        pkt = _build_disconnect()
        assert pkt == bytes([0xE0, 0x00])

    def test_parse_publish_qos0(self) -> None:
        pkt = _build_publish("test/topic", b'{"key": "val"}')
        result = _parse_publish(pkt)
        assert result is not None
        topic, payload, packet_id = result
        assert topic == "test/topic"
        assert json.loads(payload) == {"key": "val"}
        assert packet_id is None

    def test_parse_publish_not_publish(self) -> None:
        # CONNACK packet (type 2)
        result = _parse_publish(bytes([0x20, 0x02, 0x00, 0x00]))
        assert result is None


class TestMqttClientInit:
    def test_default_state(self, mock_auth: AsyncMock) -> None:
        client = MqttClient(mock_auth)
        assert client.connected is False


class TestHandleMessage:
    async def test_shadow_callback_called(self, mock_auth: AsyncMock) -> None:
        callback = MagicMock()
        client = MqttClient(mock_auth, on_shadow_update=callback)

        topic = "$aws/things/client123/shadow/get/accepted"
        payload = json.dumps({"state": {"reported": {"light": {"on": 1}}}}).encode()

        await client._handle_message(topic, payload)
        callback.assert_called_once()
        args = callback.call_args
        assert args[0][0] == "client123"
        assert args[0][1]["state"]["reported"]["light"]["on"] == 1

    async def test_async_shadow_callback(self, mock_auth: AsyncMock) -> None:
        callback = AsyncMock()
        client = MqttClient(mock_auth, on_shadow_update=callback)

        topic = "$aws/things/client456/shadow/update/delta"
        payload = json.dumps({"state": {"light": {"lv": 50}}}).encode()

        await client._handle_message(topic, payload)
        callback.assert_called_once()

    async def test_invalid_json_ignored(self, mock_auth: AsyncMock) -> None:
        callback = MagicMock()
        client = MqttClient(mock_auth, on_shadow_update=callback)

        await client._handle_message(
            "$aws/things/client123/shadow/get/accepted",
            b"not json",
        )
        callback.assert_not_called()


class TestPublishShadowUpdate:
    async def test_raises_when_not_connected(self, mock_auth: AsyncMock) -> None:
        client = MqttClient(mock_auth)
        with pytest.raises(RuntimeError, match="not connected"):
            await client.publish_shadow_update("thing1", {"light": {"on": 1}})

    async def test_publishes_correct_payload(self, mock_auth: AsyncMock) -> None:
        client = MqttClient(mock_auth)
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True

        await client.publish_shadow_update("thing1", {"light": {"on": 1}})

        mock_ws.send.assert_called_once()
        sent_bytes = mock_ws.send.call_args[0][0]
        # Parse the PUBLISH packet we sent
        parsed = _parse_publish(sent_bytes)
        assert parsed is not None
        topic, payload_bytes, _ = parsed
        assert topic == "$aws/things/thing1/shadow/update"
        payload = json.loads(payload_bytes)
        assert payload == {"state": {"desired": {"light": {"on": 1}}}}


class TestDisconnect:
    async def test_disconnect_cleans_up(self, mock_auth: AsyncMock) -> None:
        client = MqttClient(mock_auth)
        client._ws = AsyncMock()
        client._connected = True
        client._listen_task = asyncio.create_task(asyncio.sleep(100))
        client._keepalive_task = asyncio.create_task(asyncio.sleep(100))

        await client.disconnect()
        assert client._ws is None
        assert client._listen_task is None
        assert client._keepalive_task is None
        assert client.connected is False

    async def test_disconnect_when_not_connected(self, mock_auth: AsyncMock) -> None:
        client = MqttClient(mock_auth)
        await client.disconnect()  # should not raise


class TestConnectionCallback:
    async def test_sync_callback(self, mock_auth: AsyncMock) -> None:
        callback = MagicMock()
        client = MqttClient(mock_auth, on_connection_state=callback)
        await client._notify_connection(True)
        callback.assert_called_once_with(True)

    async def test_async_callback(self, mock_auth: AsyncMock) -> None:
        callback = AsyncMock()
        client = MqttClient(mock_auth, on_connection_state=callback)
        await client._notify_connection(False)
        callback.assert_called_once_with(False)
