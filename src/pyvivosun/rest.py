"""REST client for Vivosun cloud API."""

from __future__ import annotations

import uuid
from typing import Any, cast

import aiohttp

from .const import (
    AWS_IDENTITY_ENDPOINT,
    BASE_URL,
    COGNITO_URL,
    DEVICE_LIST_ENDPOINT,
    LOGIN_ENDPOINT,
    POINT_LOG_ENDPOINT,
    REQUEST_TIMEOUT,
    SP_APP_ID,
)
from .exceptions import ApiError, AuthenticationError


class RestClient:
    """Low-level async REST client for the Vivosun API."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owned_session = session is None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owned_session = True
        return self._session

    async def close(self) -> None:
        if self._owned_session and self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        session = await self._ensure_session()
        url = f"{BASE_URL}{endpoint}"
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
        if json is not None:
            kwargs["json"] = json

        async with session.request(method, url, **kwargs) as resp:
            data: dict[str, Any] = await resp.json()

        if not data.get("success", False):
            msg = data.get("message", "Unknown error")
            code = data.get("code", resp.status)
            if code in (401, 403) or "token" in str(msg).lower():
                raise AuthenticationError(str(msg))
            raise ApiError(code, str(msg))

        return cast(dict[str, Any], data.get("data", data))

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate and return token data."""
        return await self._request(
            "POST",
            LOGIN_ENDPOINT,
            json={
                "email": email,
                "password": password,
                "spAppId": SP_APP_ID,
                "spClientId": str(uuid.uuid4()),
                "spSessionId": str(uuid.uuid4()),
            },
        )

    async def get_device_list(
        self, headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Fetch all devices."""
        result = await self._request(
            "GET", DEVICE_LIST_ENDPOINT, headers=headers
        )
        # Response has deviceGroup with category arrays (e.g. "GROW")
        devices: list[dict[str, Any]] = []
        device_group = result.get("deviceGroup", {})
        if isinstance(device_group, dict):
            for _category, dev_list in device_group.items():
                if isinstance(dev_list, list):
                    devices.extend(dev_list)
        elif isinstance(result, list):
            devices = result
        return devices

    async def get_aws_identity(
        self, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Fetch AWS IoT Cognito identity (awsIdentityId + awsOpenIdToken)."""
        return await self._request(
            "POST",
            AWS_IDENTITY_ENDPOINT,
            json={"awsIdentityId": "", "attachPolicy": True},
            headers=headers,
        )

    async def get_cognito_credentials(
        self, identity_id: str, open_id_token: str
    ) -> dict[str, Any]:
        """Exchange Cognito identity for temporary AWS credentials."""
        session = await self._ensure_session()
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        async with session.post(
            COGNITO_URL,
            json={
                "IdentityId": identity_id,
                "Logins": {
                    "cognito-identity.amazonaws.com": open_id_token,
                },
            },
            headers={
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": (
                    "AWSCognitoIdentityService.GetCredentialsForIdentity"
                ),
            },
            timeout=timeout,
        ) as resp:
            data: dict[str, Any] = await resp.json(
                content_type=None
            )

        if "Credentials" not in data:
            raise ApiError(
                resp.status,
                data.get("message", "Failed to get Cognito credentials"),
            )
        return data

    async def get_point_log(
        self,
        headers: dict[str, str],
        device_id: str,
        scene_id: int,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        time_level: str = "ONE_MINUTE",
    ) -> list[dict[str, Any]]:
        """Fetch telemetry point log data.

        Args:
            headers: Auth headers from AuthManager.get_rest_headers().
            device_id: The device ID.
            scene_id: The scene ID (integer).
            start_time: Unix epoch start (default: 1 hour ago).
            end_time: Unix epoch end (default: now).
            time_level: Aggregation granularity. Valid values:
                ONE_MINUTE, FIVE_MINUTES, FIFTEEN_MINUTES,
                THIRTY_MINUTES, ONE_HOUR.
        """
        import time

        now = int(time.time())
        result = await self._request(
            "POST",
            POINT_LOG_ENDPOINT,
            json={
                "deviceId": device_id,
                "sceneId": scene_id,
                "startTime": start_time or (now - 3600),
                "endTime": end_time or now,
                "reportType": 0,
                "orderBy": "asc",
                "timeLevel": time_level,
            },
            headers=headers,
        )
        if isinstance(result, list):
            return cast(list[dict[str, Any]], result)
        return cast(
            list[dict[str, Any]],
            result.get("iotDataLogList", result.get("list", [])),
        )
