"""REST client for Vivosun cloud API."""

from __future__ import annotations

import hashlib
import json as json_module
import secrets
import string
import time
import uuid
from typing import Any, cast

import aiohttp

from .const import (
    API_PROTOCOL_VERSION,
    APP_VERSION,
    AWS_IDENTITY_ENDPOINT,
    BASE_URL,
    COGNITO_URL,
    DEVICE_LIST_ENDPOINT,
    LOGIN_ENDPOINT,
    PLAN_LIST_ENDPOINT,
    POINT_LOG_ENDPOINT,
    REQUEST_TIMEOUT,
    SERVER_PLATFORM,
    SP_APP_ID,
)
from .exceptions import ApiError, AuthenticationError

_REQUEST_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits
_AES_KEY_LENGTHS = (16, 24, 32)
_IV_LENGTH = 16


def _encrypt_protected_body(
    plaintext: bytes, *, timestamp_ms: int
) -> tuple[str, str, bytes]:
    """Return the current Android-app envelope for authenticated POST bodies.

    The request code tells the service how to derive the per-request AES-CBC
    key and IV. It contains no credential material.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    timestamp_text = str(timestamp_ms)
    timestamp_digest = hashlib.md5(timestamp_text.encode()).hexdigest()  # noqa: S324
    key_length = secrets.choice(_AES_KEY_LENGTHS)
    key_start = secrets.randbelow(len(timestamp_digest) - key_length + 1)
    key_end = key_start + key_length
    key = timestamp_digest[key_start:key_end].encode()

    salt_length = _IV_LENGTH + secrets.randbelow(84)
    salt = "".join(secrets.choice(_REQUEST_ALPHABET) for _ in range(salt_length))
    iv_start = secrets.randbelow(salt_length - _IV_LENGTH + 1)
    iv_end = iv_start + _IV_LENGTH
    iv = salt[iv_start:iv_end].encode()

    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    request_code = f"AC5-{key_start}-{key_end}-{iv_start}-{iv_end}-{salt}"
    body = json_module.dumps(
        {"content": ciphertext.hex()}, separators=(",", ":")
    ).encode()
    return timestamp_text, request_code, body


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

        request_headers = {
            "Server-Platform": SERVER_PLATFORM,
            "Api-Version": API_PROTOCOL_VERSION,
            "App-Version": APP_VERSION,
        }
        if headers:
            request_headers.update(headers)

        kwargs: dict[str, Any] = {"headers": request_headers, "timeout": timeout}
        if json is not None:
            body = json_module.dumps(json, separators=(",", ":")).encode()
            if method.upper() == "POST" and endpoint != LOGIN_ENDPOINT:
                request_time, request_code, body = _encrypt_protected_body(
                    body, timestamp_ms=int(time.time() * 1000)
                )
                request_headers["Request-Time"] = request_time
                request_headers["Request-Code"] = request_code
            request_headers["Content-Type"] = "application/json"
            kwargs["data"] = body

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

    async def get_plan_list(
        self, headers: dict[str, str], scene_id: int
    ) -> dict[str, Any]:
        """Fetch recipe/plan definitions for one Vivosun scene.

        This is a read-only REST request. Device control remains exclusively on
        the AWS IoT shadow transport.
        """
        return await self._request(
            "POST", PLAN_LIST_ENDPOINT, json={"sceneId": scene_id}, headers=headers
        )
