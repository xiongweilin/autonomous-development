import hashlib
import hmac
from pathlib import Path

import pytest

from autonomous_development.api.operator_security import (
    OperatorAuthenticationError,
    OperatorAuthenticator,
)


def _signature(
    secret: str,
    timestamp: int,
    request_id: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"{timestamp}\n{request_id}\n{method}\n{path}\n{digest}".encode()
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def test_operator_hmac_accepts_bound_request_and_rejects_tampering() -> None:
    authenticator = OperatorAuthenticator("operator-secret", ttl_seconds=30)
    body = b'{"requestId":"request-1"}'
    signature = _signature("operator-secret", 100, "request-1", "POST", "/v1/operator", body)

    authenticator.verify(
        timestamp="100",
        request_id="request-1",
        method="POST",
        path="/v1/operator",
        body=body,
        signature=signature,
        now=100,
    )
    with pytest.raises(OperatorAuthenticationError):
        authenticator.verify(
            timestamp="100",
            request_id="request-1",
            method="POST",
            path="/v1/operator",
            body=b"tampered",
            signature=signature,
            now=100,
        )


def test_operator_hmac_rejects_stale_and_unconfigured_requests() -> None:
    authenticator = OperatorAuthenticator("operator-secret", ttl_seconds=30)
    with pytest.raises(OperatorAuthenticationError, match="stale"):
        authenticator.verify(
            timestamp="100",
            request_id="request-1",
            method="GET",
            path="/v1/operator/status",
            body=b"",
            signature="invalid",
            now=131,
        )
    with pytest.raises(OperatorAuthenticationError, match="not configured"):
        OperatorAuthenticator(None).verify(
            timestamp="100",
            request_id="request-1",
            method="GET",
            path="/v1/operator/status",
            body=b"",
            signature="invalid",
            now=100,
        )


def test_operator_hmac_can_load_a_secret_from_a_regular_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "operator-hmac"
    secret_file.write_text("operator-secret\n", encoding="utf-8")
    authenticator = OperatorAuthenticator.from_file(secret_file, ttl_seconds=30)
    assert authenticator.configured


def test_operator_hmac_rejects_invalid_identity_and_timestamp() -> None:
    authenticator = OperatorAuthenticator("operator-secret")
    with pytest.raises(OperatorAuthenticationError, match="identity"):
        authenticator.verify(
            timestamp="100",
            request_id="坏",
            method="GET",
            path="/status",
            body=b"",
            signature="invalid",
            now=100,
        )
    with pytest.raises(OperatorAuthenticationError, match="timestamp"):
        authenticator.verify(
            timestamp="not-a-timestamp",
            request_id="request-1",
            method="GET",
            path="/status",
            body=b"",
            signature="invalid",
            now=100,
        )
