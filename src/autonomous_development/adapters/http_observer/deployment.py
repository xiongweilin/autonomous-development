from __future__ import annotations

import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from autonomous_development.domain.enums import DeploymentState
from autonomous_development.domain.models import Deployment
from autonomous_development.ports.deployment import (
    DeploymentObservationError,
    DeploymentObserver,
    DeploymentRuntime,
    DeploymentSpec,
)
from autonomous_development.ports.evidence import EvidenceStore


class HttpDeploymentObserver(DeploymentObserver):
    def __init__(
        self,
        evidence: EvidenceStore,
        *,
        interval_seconds: float = 0.5,
        request_timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if interval_seconds <= 0 or request_timeout_seconds <= 0:
            raise ValueError("observer timeouts must be positive")
        self._evidence = evidence
        self._interval_seconds = interval_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._transport = transport

    def wait_ready(
        self,
        spec: DeploymentSpec,
        runtime: DeploymentRuntime,
        *,
        health_path: str,
        readiness_path: str,
        timeout_seconds: int,
    ) -> Deployment:
        _require_loopback(runtime.base_url)
        if timeout_seconds < 1:
            raise ValueError("startup timeout must be positive")
        deadline = time.monotonic() + timeout_seconds
        attempts = 0
        last_health: int | str = "not-observed"
        last_readiness: int | str = "not-observed"

        while time.monotonic() < deadline:
            attempts += 1
            last_health = self._status(runtime.base_url + health_path)
            last_readiness = self._status(runtime.base_url + readiness_path)
            if _successful(last_health) and _successful(last_readiness):
                observed_at = datetime.now(UTC)
                evidence_ref = self._evidence.write_json(
                    "deployment-observation",
                    spec.deployment_id,
                    {
                        "deployment_id": spec.deployment_id,
                        "base_url": runtime.base_url,
                        "health_status": last_health,
                        "readiness_status": last_readiness,
                        "attempts": attempts,
                        "observed_at": observed_at.isoformat(),
                    },
                )
                return Deployment(
                    id=spec.deployment_id,
                    target_id=spec.target_id,
                    artifact_id=spec.artifact_id,
                    environment="local-candidate",
                    state=DeploymentState.READY,
                    observed_at=observed_at,
                    observation_refs=(evidence_ref,),
                )
            time.sleep(self._interval_seconds)

        evidence_ref = self._evidence.write_json(
            "deployment-observation",
            spec.deployment_id,
            {
                "deployment_id": spec.deployment_id,
                "base_url": runtime.base_url,
                "health_status": last_health,
                "readiness_status": last_readiness,
                "attempts": attempts,
                "timed_out": True,
            },
        )
        raise DeploymentObservationError(
            f"deployment did not become independently ready: {evidence_ref}"
        )

    def _status(self, url: str) -> int | str:
        try:
            with httpx.Client(
                timeout=self._request_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = client.get(url)
                return response.status_code
        except httpx.HTTPError as exc:
            return type(exc).__name__


def _successful(value: int | str) -> bool:
    return isinstance(value, int) and 200 <= value < 300


def _require_loopback(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("V1 deployment observation is restricted to local HTTP loopback")
