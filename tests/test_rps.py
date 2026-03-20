"""Tests for experimental RPS relay discovery."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from pyvivosun.rps import (
    RpsStatus,
    build_status_query_payload,
    discover_camera_ip_via_rps,
    query_rps_status,
)


def test_build_status_query_payload() -> None:
    payload = build_status_query_payload(
        serial_number="5a8ddedd3c1e7674",
        auth_code="aaaaaaaa-13122aded",
        message_type="MSG_STATUS_LOCALQUERY_REQ",
    )

    assert payload == {
        "StatusProtocol": {
            "Header": {
                "Version": "1.0",
                "CSeq": "1",
                "MessageType": "MSG_STATUS_LOCALQUERY_REQ",
            },
            "Body": [
                {
                    "SerialNumber": "5a8ddedd3c1e7674",
                    "AuthCode": "aaaaaaaa-13122aded",
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_query_rps_status_returns_online_camera() -> None:
    response = {
        "StatusProtocol": {
            "Body": [
                {
                    "DevicePort": "34567",
                    "ServerIP": "3.73.2.109",
                    "WanIP": "78.94.212.194",
                    "ServerPort": "6510",
                    "DeviceType": "Camera",
                    "KcpEnable": "0",
                    "Status": "Online",
                    "SerialNumber": "5a8ddedd3c1e7674",
                }
            ],
            "Header": {
                "ErrorString": "Success OK",
                "Version": "1.0",
                "CSeq": "1",
                "MessageType": "MSG_STATUS_LOCALQUERY_RSP",
                "ErrorNum": "200",
            },
        }
    }

    with aioresponses() as mocked:
        mocked.post("https://pub-status.secu100.net:7605/", payload=response)
        async with aiohttp.ClientSession() as session:
            status = await query_rps_status(
                session,
                serial_number="5a8ddedd3c1e7674",
                auth_code="aaaaaaaa-13122aded",
                port=7605,
            )

    assert status == RpsStatus(
        serial_number="5a8ddedd3c1e7674",
        status="Online",
        device_type="Camera",
        server_ip="3.73.2.109",
        server_port=6510,
        device_port=34567,
        wan_ip="78.94.212.194",
        kcp_enabled=False,
    )


@pytest.mark.asyncio
async def test_query_rps_status_returns_none_for_offline_device() -> None:
    response = {
        "StatusProtocol": {
            "Body": [{"SerialNumber": "5a8ddedd3c1e7674", "Status": "Offline"}],
            "Header": {
                "ErrorString": "Success OK",
                "Version": "1.0",
                "CSeq": "1",
                "MessageType": "MSG_STATUS_LOCALQUERY_RSP",
                "ErrorNum": "200",
            },
        }
    }

    with aioresponses() as mocked:
        mocked.post("https://pub-status.secu100.net:7601/", payload=response)
        async with aiohttp.ClientSession() as session:
            status = await query_rps_status(
                session,
                serial_number="5a8ddedd3c1e7674",
                auth_code="aaaaaaaa103122aded",
                port=7601,
            )

    assert status is None


@pytest.mark.asyncio
async def test_query_rps_status_disables_tls_verification_for_vendor_status_host(
) -> None:
    class _Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def json(self):
            return {
                "StatusProtocol": {
                    "Body": [{"SerialNumber": "5a8ddedd3c1e7674", "Status": "Offline"}],
                    "Header": {"ErrorNum": "200"},
                }
            }

    class _Session:
        def __init__(self) -> None:
            self.kwargs = None

        def post(self, url: str, **kwargs):
            self.url = url
            self.kwargs = kwargs
            return _Response()

    session = _Session()

    status = await query_rps_status(
        session,  # type: ignore[arg-type]
        serial_number="5a8ddedd3c1e7674",
        auth_code="aaaaaaaa-13122aded",
        port=7605,
    )

    assert status is None
    assert session.kwargs is not None
    assert session.kwargs["ssl"] is False


@pytest.mark.asyncio
async def test_discover_camera_ip_via_rps_prefers_wifi_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_query(
        session: aiohttp.ClientSession,
        *,
        serial_number: str,
        auth_code: str,
        port: int,
        message_type: str = "MSG_STATUS_LOCALQUERY_REQ",
    ) -> RpsStatus | None:
        _ = session, auth_code, port, message_type
        return RpsStatus(
            serial_number=serial_number,
            status="Online",
            device_type="Camera",
            server_ip="3.73.2.109",
            server_port=6510,
            device_port=34567,
            wan_ip="78.94.212.194",
            kcp_enabled=False,
        )

    monkeypatch.setattr("pyvivosun.rps.query_rps_status", _fake_query)
    monkeypatch.setattr(
        "pyvivosun.rps._query_network_info_over_rps",
        lambda **kwargs: {"wifi_ip": "10.0.15.202", "common_ip": "192.168.1.10"},
    )

    async with aiohttp.ClientSession() as session:
        ip = await discover_camera_ip_via_rps(
            session,
            serial_number="5a8ddedd3c1e7674",
            username="abjd",
            password="4kt5em",
            auth_codes=("aaaaaaaa-13122aded",),
        )

    assert ip == "10.0.15.202"


@pytest.mark.asyncio
async def test_discover_camera_ip_via_rps_returns_none_without_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_query(
        session: aiohttp.ClientSession,
        *,
        serial_number: str,
        auth_code: str,
        port: int,
        message_type: str = "MSG_STATUS_LOCALQUERY_REQ",
    ) -> RpsStatus | None:
        _ = session, serial_number, auth_code, port, message_type
        return None

    monkeypatch.setattr("pyvivosun.rps.query_rps_status", _fake_query)

    async with aiohttp.ClientSession() as session:
        ip = await discover_camera_ip_via_rps(
            session,
            serial_number="5a8ddedd3c1e7674",
            username="abjd",
            password="4kt5em",
            auth_codes=("aaaaaaaa-13122aded",),
        )

    assert ip is None


@pytest.mark.asyncio
async def test_discover_camera_ip_via_rps_ignores_tunnel_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_query(
        session: aiohttp.ClientSession,
        *,
        serial_number: str,
        auth_code: str,
        port: int,
        message_type: str = "MSG_STATUS_LOCALQUERY_REQ",
    ) -> RpsStatus | None:
        _ = session, auth_code, port, message_type
        return RpsStatus(
            serial_number=serial_number,
            status="Online",
            device_type="Camera",
            server_ip="3.73.2.109",
            server_port=6510,
            device_port=34567,
            wan_ip="78.94.212.194",
            kcp_enabled=False,
        )

    monkeypatch.setattr("pyvivosun.rps.query_rps_status", _fake_query)

    def _broken_query(**kwargs):
        _ = kwargs
        raise ConnectionError("timeout")

    monkeypatch.setattr("pyvivosun.rps._query_network_info_over_rps", _broken_query)

    async with aiohttp.ClientSession() as session:
        ip = await discover_camera_ip_via_rps(
            session,
            serial_number="5a8ddedd3c1e7674",
            username="abjd",
            password="4kt5em",
            auth_codes=("aaaaaaaa-13122aded",),
        )

    assert ip is None
