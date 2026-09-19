from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path


class OperatorAuthenticationError(ValueError):
    """An operator request failed the loopback transport authentication contract."""


class OperatorAuthenticator:
    def __init__(self, secret: str | None, *, ttl_seconds: int = 300) -> None:
        if ttl_seconds < 1:
            raise ValueError("operator HMAC TTL must be positive")
        self._secret = secret.strip() if secret is not None else ""
        self._ttl_seconds = ttl_seconds

    @classmethod
    def from_file(cls, path: Path | None, *, ttl_seconds: int = 300) -> OperatorAuthenticator:
        if path is None:
            return cls(None, ttl_seconds=ttl_seconds)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            return cls(None, ttl_seconds=ttl_seconds)
        value = path.read_text(encoding="utf-8").strip()
        return cls(value, ttl_seconds=ttl_seconds)

    @property
    def configured(self) -> bool:
        return bool(self._secret)

    def verify(
        self,
        *,
        timestamp: str,
        request_id: str,
        method: str,
        path: str,
        body: bytes,
        signature: str,
        now: int | None = None,
    ) -> None:
        if not self._secret:
            raise OperatorAuthenticationError("operator transport is not configured")
        if not request_id or len(request_id) > 160 or not request_id.isascii():
            raise OperatorAuthenticationError("operator request identity is invalid")
        try:
            timestamp_value = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise OperatorAuthenticationError("operator timestamp is invalid") from exc
        current = int(time.time()) if now is None else now
        if abs(current - timestamp_value) > self._ttl_seconds:
            raise OperatorAuthenticationError("operator request is stale")
        digest = hashlib.sha256(body).hexdigest()
        canonical = f"{timestamp}\n{request_id}\n{method.upper()}\n{path}\n{digest}".encode()
        expected = hmac.new(self._secret.encode(), canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.strip().lower()):
            raise OperatorAuthenticationError("operator signature is invalid")
