"""Tests for AuthManager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from pyvivosun.auth import AuthManager
from pyvivosun.exceptions import AuthenticationError


@pytest.fixture
def mock_rest() -> AsyncMock:
    rest = AsyncMock()
    rest.login.return_value = {
        "accessToken": "at123",
        "loginToken": "lt123",
        "refreshToken": "rt123",
        "userId": "42",
    }
    rest.get_aws_identity.return_value = {
        "awsHost": "iot.example.com",
        "awsRegion": "us-east-2",
        "awsIdentityId": "id123",
        "awsOpenIdToken": "tok123",
        "awsPort": 443,
    }
    rest.get_cognito_credentials.return_value = {
        "Credentials": {
            "AccessKeyId": "AKIA",
            "SecretKey": "secret",
            "SessionToken": "token",
            "Expiration": (datetime.now(UTC) + timedelta(hours=1)).timestamp(),
        },
        "IdentityId": "id123",
    }
    return rest


@pytest.fixture
def auth(mock_rest: AsyncMock) -> AuthManager:
    return AuthManager(mock_rest, "user@example.com", "password123")


class TestEnsureAuthenticated:
    async def test_calls_login(self, auth: AuthManager, mock_rest: AsyncMock) -> None:
        await auth.ensure_authenticated()
        mock_rest.login.assert_called_once_with("user@example.com", "password123")
        assert auth.tokens is not None
        assert auth.tokens.access_token == "at123"

    async def test_does_not_login_twice(
        self, auth: AuthManager, mock_rest: AsyncMock
    ) -> None:
        await auth.ensure_authenticated()
        await auth.ensure_authenticated()
        mock_rest.login.assert_called_once()


class TestGetRestHeaders:
    async def test_returns_headers(self, auth: AuthManager) -> None:
        await auth.ensure_authenticated()
        headers = auth.get_rest_headers()
        assert headers == {
            "login-token": "lt123",
            "access-token": "at123",
        }

    def test_raises_if_not_authenticated(self, auth: AuthManager) -> None:
        with pytest.raises(AuthenticationError):
            auth.get_rest_headers()


class TestGetAwsCredentials:
    async def test_fetches_credentials(
        self, auth: AuthManager, mock_rest: AsyncMock
    ) -> None:
        creds = await auth.get_aws_credentials()
        assert creds.host == "iot.example.com"
        assert creds.access_key_id == "AKIA"
        assert creds.region == "us-east-2"
        mock_rest.get_aws_identity.assert_called_once()
        mock_rest.get_cognito_credentials.assert_called_once_with("id123", "tok123")

    async def test_caches_credentials(
        self, auth: AuthManager, mock_rest: AsyncMock
    ) -> None:
        creds1 = await auth.get_aws_credentials()
        creds2 = await auth.get_aws_credentials()
        assert creds1 is creds2
        mock_rest.get_aws_identity.assert_called_once()

    async def test_refreshes_when_near_expiry(
        self, auth: AuthManager, mock_rest: AsyncMock
    ) -> None:
        creds = await auth.get_aws_credentials()
        # Manually set expiry to near-future
        creds.expires_at = datetime.now(UTC) + timedelta(seconds=60)
        creds2 = await auth.get_aws_credentials()
        assert creds2 is not creds
        assert mock_rest.get_aws_identity.call_count == 2


class TestBackgroundRefresh:
    async def test_start_and_stop(self, auth: AuthManager) -> None:
        await auth.ensure_authenticated()
        await auth.start_credential_refresh()
        assert auth._refresh_task is not None
        await auth.stop()
        assert auth._refresh_task is None
