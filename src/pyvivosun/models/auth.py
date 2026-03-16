"""Authentication models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TokenSet:
    """REST API authentication tokens."""

    access_token: str
    login_token: str
    refresh_token: str
    user_id: str


@dataclass
class AwsCredentials:
    """AWS IoT Core credentials for MQTT connection."""

    host: str
    region: str
    access_key_id: str
    secret_access_key: str
    session_token: str
    port: int
    expires_at: datetime
