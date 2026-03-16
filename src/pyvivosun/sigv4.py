"""AWS SigV4 presigned WebSocket URL builder for AWS IoT Core.

Uses only stdlib (hashlib, hmac, urllib.parse). No boto3 dependency.

Key quirk: the session token is excluded from the canonical query string
during signing but appended to the final URL afterward. This is an
AWS IoT-specific requirement.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(
    key: str, date_stamp: str, region: str, service: str
) -> bytes:
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def build_presigned_wss_url(
    host: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str,
    port: int = 443,
    *,
    now: datetime | None = None,
) -> str:
    """Build a SigV4 presigned WSS URL for AWS IoT MQTT.

    Args:
        host: AWS IoT endpoint hostname.
        region: AWS region (e.g. 'us-east-1').
        access_key: AWS access key ID.
        secret_key: AWS secret access key.
        session_token: AWS session token (STS).
        port: WebSocket port (default 443).
        now: Override current time for testing.

    Returns:
        Presigned wss:// URL for MQTT WebSocket connection.
    """
    if now is None:
        now = datetime.now(UTC)

    service = "iotdevicegateway"
    method = "GET"
    path = "/mqtt"
    algorithm = "AWS4-HMAC-SHA256"

    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    credential = f"{access_key}/{credential_scope}"

    # Canonical query string — session token is EXCLUDED during signing
    query_params = {
        "X-Amz-Algorithm": algorithm,
        "X-Amz-Credential": credential,
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": "86400",
        "X-Amz-SignedHeaders": "host",
    }
    canonical_querystring = urllib.parse.urlencode(
        sorted(query_params.items()), quote_via=urllib.parse.quote
    )

    # Canonical headers
    canonical_headers = f"host:{host}\n"
    signed_headers = "host"

    # Empty payload hash
    payload_hash = hashlib.sha256(b"").hexdigest()

    # Canonical request
    canonical_request = "\n".join([
        method,
        path,
        canonical_querystring,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    # String to sign
    string_to_sign = "\n".join([
        algorithm,
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    # Calculate signature
    signing_key = _get_signature_key(secret_key, date_stamp, region, service)
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # Build final URL — session token appended AFTER signing
    signed_querystring = (
        f"{canonical_querystring}"
        f"&X-Amz-Signature={signature}"
        f"&X-Amz-Security-Token={urllib.parse.quote(session_token, safe='')}"
    )

    return f"wss://{host}:{port}{path}?{signed_querystring}"
