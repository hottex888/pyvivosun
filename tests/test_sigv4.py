"""Tests for SigV4 presigned URL builder."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from pyvivosun.sigv4 import build_presigned_wss_url


class TestBuildPresignedWssUrl:
    def _build_url(self) -> str:
        return build_presigned_wss_url(
            host="iot.us-east-1.amazonaws.com",
            region="us-east-1",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            session_token="FwoGZXIvYXdzEA0aDExampleSessionToken",
            port=443,
            now=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        )

    def test_returns_wss_scheme(self) -> None:
        url = self._build_url()
        parsed = urlparse(url)
        assert parsed.scheme == "wss"

    def test_correct_host_and_port(self) -> None:
        url = self._build_url()
        parsed = urlparse(url)
        assert parsed.hostname == "iot.us-east-1.amazonaws.com"
        assert parsed.port == 443

    def test_mqtt_path(self) -> None:
        url = self._build_url()
        parsed = urlparse(url)
        assert parsed.path == "/mqtt"

    def test_contains_algorithm(self) -> None:
        url = self._build_url()
        params = parse_qs(urlparse(url).query)
        assert params["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]

    def test_contains_credential(self) -> None:
        url = self._build_url()
        params = parse_qs(urlparse(url).query)
        cred = params["X-Amz-Credential"][0]
        assert cred.startswith("AKIAIOSFODNN7EXAMPLE/")
        assert "us-east-1/iotdevicegateway/aws4_request" in cred

    def test_contains_date(self) -> None:
        url = self._build_url()
        params = parse_qs(urlparse(url).query)
        assert params["X-Amz-Date"] == ["20250115T120000Z"]

    def test_contains_signature(self) -> None:
        url = self._build_url()
        params = parse_qs(urlparse(url).query)
        sig = params["X-Amz-Signature"][0]
        assert len(sig) == 64  # hex-encoded SHA256
        assert all(c in "0123456789abcdef" for c in sig)

    def test_session_token_appended(self) -> None:
        url = self._build_url()
        params = parse_qs(urlparse(url).query)
        assert "X-Amz-Security-Token" in params
        assert params["X-Amz-Security-Token"] == [
            "FwoGZXIvYXdzEA0aDExampleSessionToken"
        ]

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        url1 = self._build_url()
        url2 = self._build_url()
        assert url1 == url2

    def test_different_time_different_signature(self) -> None:
        url1 = build_presigned_wss_url(
            host="iot.us-east-1.amazonaws.com",
            region="us-east-1",
            access_key="AKID",
            secret_key="SECRET",
            session_token="TOKEN",
            now=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        url2 = build_presigned_wss_url(
            host="iot.us-east-1.amazonaws.com",
            region="us-east-1",
            access_key="AKID",
            secret_key="SECRET",
            session_token="TOKEN",
            now=datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC),
        )
        sig1 = parse_qs(urlparse(url1).query)["X-Amz-Signature"][0]
        sig2 = parse_qs(urlparse(url2).query)["X-Amz-Signature"][0]
        assert sig1 != sig2
