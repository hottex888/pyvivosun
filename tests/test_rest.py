"""Tests for REST client."""

import aiohttp
import pytest
from aioresponses import aioresponses

from pyvivosun.const import BASE_URL, COGNITO_URL
from pyvivosun.exceptions import ApiError, AuthenticationError
from pyvivosun.rest import RestClient


@pytest.fixture
def mock_aio():
    with aioresponses() as m:
        yield m


@pytest.fixture
async def rest_client():
    client = RestClient()
    yield client
    await client.close()


class TestLogin:
    async def test_successful_login(self, mock_aio, rest_client) -> None:
        mock_aio.post(
            f"{BASE_URL}/user/login",
            payload={
                "success": True,
                "data": {
                    "accessToken": "at123",
                    "loginToken": "lt123",
                    "refreshToken": "rt123",
                    "userId": "42",
                },
            },
        )
        result = await rest_client.login("user@example.com", "pass")
        assert result["accessToken"] == "at123"
        assert result["userId"] == "42"

    async def test_login_failure(self, mock_aio, rest_client) -> None:
        mock_aio.post(
            f"{BASE_URL}/user/login",
            payload={
                "success": False,
                "code": 401,
                "message": "Invalid token",
            },
        )
        with pytest.raises(AuthenticationError):
            await rest_client.login("bad@example.com", "wrong")


class TestGetDeviceList:
    async def test_returns_devices_from_groups(self, mock_aio, rest_client) -> None:
        mock_aio.get(
            f"{BASE_URL}/iot/device/getTotalList",
            payload={
                "success": True,
                "data": {
                    "deviceGroup": {
                        "GROW": [
                            {"deviceId": "d1", "clientId": "c1", "name": "Hub"},
                            {"deviceId": "d2", "clientId": "c2", "name": "Light"},
                        ]
                    }
                },
            },
        )
        headers = {"login-token": "lt", "access-token": "at"}
        result = await rest_client.get_device_list(headers)
        assert len(result) == 2
        assert result[0]["deviceId"] == "d1"
        assert result[1]["name"] == "Light"


class TestGetAwsIdentity:
    async def test_returns_identity(self, mock_aio, rest_client) -> None:
        mock_aio.post(
            f"{BASE_URL}/iot/user/awsIdentity",
            payload={
                "success": True,
                "data": {
                    "awsHost": "iot.example.com",
                    "awsRegion": "us-east-2",
                    "awsIdentityId": "id123",
                    "awsOpenIdToken": "tok123",
                    "awsPort": 443,
                },
            },
        )
        headers = {"login-token": "lt", "access-token": "at"}
        result = await rest_client.get_aws_identity(headers)
        assert result["awsHost"] == "iot.example.com"
        assert result["awsIdentityId"] == "id123"


class TestGetCognitoCredentials:
    async def test_returns_credentials(self, mock_aio, rest_client) -> None:
        mock_aio.post(
            COGNITO_URL,
            payload={
                "Credentials": {
                    "AccessKeyId": "AKIA...",
                    "SecretKey": "secret",
                    "SessionToken": "token",
                    "Expiration": 1700000000.0,
                },
                "IdentityId": "id123",
            },
        )
        result = await rest_client.get_cognito_credentials("id123", "tok123")
        assert result["Credentials"]["AccessKeyId"] == "AKIA..."

    async def test_missing_credentials_raises(self, mock_aio, rest_client) -> None:
        mock_aio.post(
            COGNITO_URL,
            payload={"message": "Invalid identity"},
        )
        with pytest.raises(ApiError):
            await rest_client.get_cognito_credentials("bad", "bad")


class TestGetPointLog:
    async def test_returns_telemetry(self, mock_aio, rest_client) -> None:
        mock_aio.post(
            f"{BASE_URL}/iot/data/getPointLog",
            payload={
                "success": True,
                "data": {
                    "iotDataLogList": [
                        {"inTemp": 2500, "inHumi": 6000, "inVpd": 120},
                    ]
                },
            },
        )
        headers = {"login-token": "lt", "access-token": "at"}
        result = await rest_client.get_point_log(headers, "d1", 66078)
        assert len(result) == 1
        assert result[0]["inTemp"] == 2500


class TestApiError:
    async def test_non_auth_error(self, mock_aio, rest_client) -> None:
        mock_aio.post(
            f"{BASE_URL}/user/login",
            payload={
                "success": False,
                "code": 500,
                "message": "Internal error",
            },
        )
        with pytest.raises(ApiError) as exc_info:
            await rest_client.login("u@e.com", "p")
        assert exc_info.value.status == 500


class TestSessionManagement:
    async def test_uses_provided_session(self, mock_aio) -> None:
        async with aiohttp.ClientSession() as session:
            client = RestClient(session=session)
            mock_aio.post(
                f"{BASE_URL}/user/login",
                payload={
                    "success": True,
                    "data": {
                        "accessToken": "at",
                        "loginToken": "lt",
                        "refreshToken": "rt",
                        "userId": "1",
                    },
                },
            )
            result = await client.login("u@e.com", "p")
            assert result["accessToken"] == "at"
            await client.close()
            assert not session.closed
