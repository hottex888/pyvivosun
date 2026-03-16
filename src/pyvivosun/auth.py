"""Authentication manager for REST tokens and AWS credentials."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from .const import AWS_CREDENTIAL_REFRESH_INTERVAL, AWS_CREDENTIAL_REFRESH_SKEW
from .exceptions import AuthenticationError
from .models.auth import AwsCredentials, TokenSet
from .rest import RestClient

_LOGGER = logging.getLogger(__name__)


class AuthManager:
    """Manages REST auth tokens and AWS IoT credentials lifecycle."""

    def __init__(self, rest: RestClient, email: str, password: str) -> None:
        self._rest = rest
        self._email = email
        self._password = password
        self._tokens: TokenSet | None = None
        self._aws_credentials: AwsCredentials | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def tokens(self) -> TokenSet | None:
        return self._tokens

    @property
    def aws_credentials(self) -> AwsCredentials | None:
        return self._aws_credentials

    async def ensure_authenticated(self) -> None:
        """Login if we have no tokens."""
        if self._tokens is not None:
            return
        await self._login()

    async def _login(self) -> None:
        data = await self._rest.login(self._email, self._password)
        self._tokens = TokenSet(
            access_token=data["accessToken"],
            login_token=data["loginToken"],
            refresh_token=data.get("refreshToken", ""),
            user_id=str(data["userId"]),
        )
        _LOGGER.debug("Authenticated as user %s", self._tokens.user_id)

    def get_rest_headers(self) -> dict[str, str]:
        """Return auth headers for REST calls. Re-login if needed."""
        if self._tokens is None:
            raise AuthenticationError("Not authenticated — call ensure_authenticated()")
        return {
            "login-token": self._tokens.login_token,
            "access-token": self._tokens.access_token,
        }

    async def get_aws_credentials(self) -> AwsCredentials:
        """Return valid AWS credentials, refreshing if near expiry."""
        if self._aws_credentials is not None:
            now = datetime.now(UTC)
            if self._aws_credentials.expires_at - now > timedelta(
                seconds=AWS_CREDENTIAL_REFRESH_SKEW
            ):
                return self._aws_credentials

        await self._refresh_aws_credentials()
        assert self._aws_credentials is not None
        return self._aws_credentials

    async def _refresh_aws_credentials(self) -> None:
        await self.ensure_authenticated()
        headers = self.get_rest_headers()

        # Step 1: Get Cognito identity from Vivosun API
        identity_data = await self._rest.get_aws_identity(headers)
        identity_id = identity_data["awsIdentityId"]
        open_id_token = identity_data["awsOpenIdToken"]
        host = identity_data["awsHost"]
        region = identity_data.get("awsRegion", "us-east-2")
        port = int(identity_data.get("awsPort", 443))

        # Step 2: Exchange for temporary AWS credentials via Cognito
        cognito_data = await self._rest.get_cognito_credentials(
            identity_id, open_id_token
        )
        creds = cognito_data["Credentials"]

        expires_at = datetime.now(UTC) + timedelta(hours=1)
        if "Expiration" in creds:
            # Cognito returns epoch seconds as a float
            expires_at = datetime.fromtimestamp(creds["Expiration"], tz=UTC)

        self._aws_credentials = AwsCredentials(
            host=host,
            region=region,
            access_key_id=creds["AccessKeyId"],
            secret_access_key=creds["SecretKey"],
            session_token=creds["SessionToken"],
            port=port,
            expires_at=expires_at,
        )
        _LOGGER.debug("AWS credentials refreshed, expires %s", expires_at)

    async def start_credential_refresh(self) -> None:
        """Start background task that refreshes AWS credentials periodically."""
        if self._refresh_task is not None:
            return
        self._refresh_task = asyncio.create_task(self._credential_refresh_loop())

    async def _credential_refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(AWS_CREDENTIAL_REFRESH_INTERVAL)
            try:
                await self._refresh_aws_credentials()
            except Exception:
                _LOGGER.exception("Failed to refresh AWS credentials")

    async def stop(self) -> None:
        """Cancel the background refresh task."""
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None
