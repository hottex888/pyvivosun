"""Experimental RPS relay discovery for GrowCam devices."""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import socket
import ssl
import struct
from collections.abc import Mapping
from typing import Any, cast

import aiohttp

from .exceptions import AuthenticationError, ConnectionError
from .models.camera import CameraNetworkInfo
from .models.rps import RpsStatus

RPS_STATUS_HOST = "https://pub-status.secu100.net"
RPS_STATUS_PORTS = (7601, 7605)
DEFAULT_RPS_AUTH_CODES = (
    "aaaaaaaa103122aded",
    "aaaaaaaa-13122aded",
)
DEFAULT_RPS_CLIENT_TOKEN = "c_gmd06vxvj60kbzdn7en8a032"


def build_status_query_payload(
    *,
    serial_number: str,
    auth_code: str,
    message_type: str = "MSG_STATUS_LOCALQUERY_REQ",
) -> dict[str, Any]:
    """Build the observed status query payload used by the mobile app."""
    return {
        "StatusProtocol": {
            "Header": {
                "Version": "1.0",
                "CSeq": "1",
                "MessageType": message_type,
            },
            "Body": [
                {
                    "SerialNumber": serial_number,
                    "AuthCode": auth_code,
                }
            ],
        }
    }


async def query_rps_status(
    session: aiohttp.ClientSession,
    *,
    serial_number: str,
    auth_code: str,
    port: int,
    message_type: str = "MSG_STATUS_LOCALQUERY_REQ",
) -> RpsStatus | None:
    """Query a single RPS status endpoint for one camera serial."""
    payload = build_status_query_payload(
        serial_number=serial_number,
        auth_code=auth_code,
        message_type=message_type,
    )
    async with session.post(
        f"{RPS_STATUS_HOST}:{port}/",
        json=payload,
        ssl=False,
    ) as response:
        data = await response.json()

    body = data.get("StatusProtocol", {}).get("Body", [])
    if not isinstance(body, list) or not body:
        return None
    item = body[0]
    if not isinstance(item, dict):
        return None
    if item.get("Status") != "Online":
        return None
    return RpsStatus(
        serial_number=str(item.get("SerialNumber", serial_number)),
        status=str(item.get("Status", "Unknown")),
        device_type=_as_str(item.get("DeviceType")),
        server_ip=_as_str(item.get("ServerIP")),
        server_port=_as_int(item.get("ServerPort")),
        device_port=_as_int(item.get("DevicePort")),
        wan_ip=_as_str(item.get("WanIP")),
        kcp_enabled=_as_bool(item.get("KcpEnable")),
    )


async def discover_camera_ip_via_rps(
    session: aiohttp.ClientSession,
    *,
    serial_number: str,
    username: str,
    password: str,
    auth_codes: tuple[str, ...] = DEFAULT_RPS_AUTH_CODES,
    client_token: str = DEFAULT_RPS_CLIENT_TOKEN,
) -> str | None:
    """Discover the camera LAN IP through the experimental RPS path."""
    for auth_code in auth_codes:
        for port in RPS_STATUS_PORTS:
            status = await query_rps_status(
                session,
                serial_number=serial_number,
                auth_code=auth_code,
                port=port,
            )
            if status is None:
                continue
            try:
                network_info = await asyncio.to_thread(
                    _query_network_info_over_rps,
                    status=status,
                    username=username,
                    password=password,
                    auth_code=auth_code,
                    client_token=client_token,
                )
            except Exception:
                continue
            if isinstance(network_info, CameraNetworkInfo):
                return network_info.wifi_ip or network_info.common_ip
            if isinstance(network_info, Mapping):
                wifi_ip = network_info.get("wifi_ip")
                common_ip = network_info.get("common_ip")
                if isinstance(wifi_ip, str):
                    return wifi_ip
                if isinstance(common_ip, str):
                    return common_ip
    return None


def _query_network_info_over_rps(
    *,
    status: RpsStatus,
    username: str,
    password: str,
    auth_code: str,
    client_token: str,
) -> CameraNetworkInfo:
    """Open an RpsCmd tunnel and fetch camera network config."""
    if not status.server_ip or status.server_port is None or status.device_port is None:
        raise ConnectionError("RPS status response is missing relay connection details")

    tunnel = _open_rps_cmd_socket(
        server_ip=status.server_ip,
        server_port=status.server_port,
        serial_number=status.serial_number,
        device_port=status.device_port,
        auth_code=auth_code,
        client_token=client_token,
    )
    try:
        _drain_unsolicited_messages(tunnel)
        camera_class = _get_dvrip_cam_class()
        camera = camera_class("0.0.0.0", user=username, password=password)
        camera.socket = tunnel
        camera.timeout = 10
        camera.socket_send = camera.tcp_socket_send
        camera.socket_recv = camera.tcp_socket_recv
        if not camera.login():
            raise AuthenticationError("RPS camera login failed")
        wifi = camera.get_command("NetWork.Wifi", 1042)
        common = camera.get_command("NetWork.NetCommon", 1042)
        camera.close()
    except Exception:
        tunnel.close()
        raise

    wifi_mapping = wifi if isinstance(wifi, Mapping) else {}
    common_mapping = common if isinstance(common, Mapping) else {}
    return CameraNetworkInfo(
        wifi_ip=_decode_hex_ip(_as_str(wifi_mapping.get("HostIP"))),
        common_ip=_decode_hex_ip(_as_str(common_mapping.get("HostIP"))),
        wifi_gateway=_decode_hex_ip(_as_str(wifi_mapping.get("GateWay"))),
        common_gateway=_decode_hex_ip(_as_str(common_mapping.get("GateWay"))),
        tcp_port=_as_int(common_mapping.get("TCPPort")),
        udp_port=_as_int(common_mapping.get("UDPPort")),
        http_port=_as_int(common_mapping.get("HttpPort")),
        ssl_port=_as_int(common_mapping.get("SSLPort")),
        ssid=_as_str(wifi_mapping.get("SSID")),
        mac=_as_str(common_mapping.get("MAC")),
    )


def _open_rps_cmd_socket(
    *,
    server_ip: str,
    server_port: int,
    serial_number: str,
    device_port: int,
    auth_code: str,
    client_token: str,
) -> socket.socket:
    """Open an RpsCmd tunnel and return a socket carrying DVRIP frames."""
    session_id = _new_session_id()
    raw_access_socket = socket.create_connection((server_ip, server_port), timeout=10)
    tls_context = ssl.create_default_context()
    tls_context.check_hostname = False
    tls_context.verify_mode = ssl.CERT_NONE
    access_socket = tls_context.wrap_socket(
        raw_access_socket,
        server_hostname=server_ip,
    )
    try:
        request_body = {
            "AgentProtocol": {
                "Body": {
                    "Authcode": auth_code,
                    "ClientToken": client_token,
                    "DestPort": str(device_port),
                    "Encrypt": "1",
                    "SerialNumber": serial_number,
                    "ServiceType": "RpsCmd",
                    "SessionId": session_id,
                    "Step": "0",
                },
                "Header": {
                    "MessageType": "MSG_CLI_NEED_CON_REQ",
                    "Version": "1.0",
                },
            }
        }
        _send_http_json(access_socket, request_body, host=server_ip)
        response = _read_http_json(access_socket)
    finally:
        access_socket.close()

    body = _mapping(_mapping(response.get("AgentProtocol")).get("Body"))
    agent_ip = _as_str(body.get("AgentServerIp"))
    agent_port = _as_int(body.get("AgentServerPort"))
    if not agent_ip or agent_port is None:
        raise ConnectionError("RPS access server did not return an agent relay host")

    tunnel = socket.create_connection((agent_ip, agent_port), timeout=10)
    try:
        handshake = _build_agent_handshake(
            auth_code=auth_code,
            client_token=client_token,
            serial_number=serial_number,
            session_id=session_id,
        )
        tunnel.sendall(handshake)
        handshake_response = _read_agent_handshake_response(tunnel)
        if _mapping(handshake_response).get("ErrorNum") != "200":
            raise ConnectionError("RPS relay handshake failed")
        tunnel.settimeout(10)
        return tunnel
    except Exception:
        tunnel.close()
        raise


def _send_http_json(sock: socket.socket, body: Mapping[str, Any], *, host: str) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode()
    request = (
        b"POST / HTTP/1.1\r\n"
        + f"Host: {host}\r\n".encode()
        + b"Content-Type: text/html\r\n"
        + f"Content-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    sock.sendall(request)


def _read_http_json(sock: socket.socket) -> dict[str, Any]:
    buffer = bytearray()
    sock.settimeout(10)
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Unexpected EOF while reading HTTP response headers")
        buffer.extend(chunk)
    header_end = buffer.index(b"\r\n\r\n") + 4
    headers = buffer[:header_end].decode("utf-8", "ignore")
    content_length = 0
    for line in headers.split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
            break
    body = buffer[header_end:]
    while len(body) < content_length:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Unexpected EOF while reading HTTP response body")
        body.extend(chunk)
    return cast(
        dict[str, Any], json.loads(body[:content_length].decode("utf-8", "ignore"))
    )


def _build_agent_handshake(
    *, auth_code: str, client_token: str, serial_number: str, session_id: str
) -> bytes:
    payload = f"{auth_code}:{client_token}:{serial_number}:{session_id}".encode()
    return base64.b64encode(payload) + b"XXEE"


def _read_agent_handshake_response(sock: socket.socket) -> dict[str, Any]:
    buffer = bytearray()
    sock.settimeout(10)
    while b"XXEE" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Unexpected EOF while reading RPS handshake response")
        buffer.extend(chunk)
    payload, _, _ = buffer.partition(b"XXEE")
    decoded = base64.b64decode(payload)
    return cast(dict[str, Any], json.loads(decoded.decode("utf-8", "ignore")))


def _drain_unsolicited_messages(sock: socket.socket, *, timeout: float = 0.3) -> None:
    """Drain unsolicited DVRIP frames such as the initial 1414 capability packet."""
    original_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        while True:
            header = sock.recv(20)
            if not header or len(header) < 20:
                break
            _, _, _, _, _, length = _unpack_dvrip_header(header)
            remaining = length
            while remaining > 0:
                chunk = sock.recv(remaining)
                if not chunk:
                    break
                remaining -= len(chunk)
    except TimeoutError:
        pass
    finally:
        sock.settimeout(original_timeout)


def _unpack_dvrip_header(header: bytes) -> tuple[int, int, int, int, int, int]:
    return struct.unpack("BB2xII2xHI", header)


def _new_session_id(length: int = 32) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str) and value in {"0", "1"}:
        return value == "1"
    return None


def _decode_hex_ip(value: str | None) -> str | None:
    if not value or not value.startswith("0x"):
        return None
    try:
        encoded = int(value, 16)
    except ValueError:
        return None
    octets = encoded.to_bytes(4, "little")
    return ".".join(str(part) for part in octets)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _get_dvrip_cam_class() -> type[Any]:
    try:
        from dvrip import DVRIPCam  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - exercised in real installs
        raise ConnectionError(
            "Experimental RPS camera discovery requires the python-dvr package"
        ) from exc
    return cast(type[Any], DVRIPCam)
