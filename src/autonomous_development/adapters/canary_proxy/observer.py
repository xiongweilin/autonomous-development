from __future__ import annotations

import math
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from autonomous_development.domain.canary import CanaryStageEvidence
from autonomous_development.domain.models import CanaryStage
from autonomous_development.ports.canary import CanaryObservationError, CanaryObserver
from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.traffic import TrafficRouteState


class ProxyCanaryObserver(CanaryObserver):
    def __init__(
        self,
        proxy_base_url: str,
        evidence: EvidenceStore,
        *,
        observation_timeout_seconds: int,
        poll_interval_seconds: float = 0.5,
        request_timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        _require_loopback(proxy_base_url)
        if observation_timeout_seconds < 1:
            raise ValueError("observation timeout must be positive")
        if poll_interval_seconds <= 0 or request_timeout_seconds <= 0:
            raise ValueError("observer polling timeouts must be positive")
        self._proxy_base_url = proxy_base_url.rstrip("/")
        self._evidence = evidence
        self._observation_timeout_seconds = observation_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._transport = transport

    def observe(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        stage: CanaryStage,
        route_state: TrafficRouteState,
    ) -> CanaryStageEvidence:
        if route_state.experiment_id != experiment_id or route_state.stage_index != stage_index:
            raise CanaryObservationError("traffic route identity does not match requested canary stage")
        if route_state.candidate_weight_percent != stage.weight_percent:
            raise CanaryObservationError("traffic route weight does not match requested canary stage")

        deadline = time.monotonic() + self._observation_timeout_seconds
        latest: dict[str, Any] | None = None
        transient_error: str | None = None
        while time.monotonic() < deadline:
            try:
                latest = self._read_snapshot(route_state.generation)
                transient_error = None
            except httpx.HTTPError as exc:
                transient_error = type(exc).__name__
            if latest is not None and _snapshot_sufficient(latest, stage):
                return self._to_evidence(
                    experiment_id,
                    stage_index,
                    stage,
                    latest,
                    timed_out=False,
                    transient_error=transient_error,
                )
            time.sleep(self._poll_interval_seconds)

        return self._to_evidence(
            experiment_id,
            stage_index,
            stage,
            latest,
            timed_out=True,
            transient_error=transient_error,
        )

    def _read_snapshot(self, generation: int) -> dict[str, Any] | None:
        with httpx.Client(
            timeout=self._request_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            response = client.get(
                f"{self._proxy_base_url}/__autodev/metrics/{generation}"
            )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise CanaryObservationError(
                f"canary proxy metrics endpoint returned {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise CanaryObservationError("canary proxy metrics response is not an object")
        return {str(key): value for key, value in payload.items()}

    def _to_evidence(
        self,
        experiment_id: str,
        stage_index: int,
        stage: CanaryStage,
        snapshot: dict[str, Any] | None,
        *,
        timed_out: bool,
        transient_error: str | None,
    ) -> CanaryStageEvidence:
        normalized = _normalize_snapshot(snapshot)
        payload: dict[str, object] = {
            "experiment_id": experiment_id,
            "stage_index": stage_index,
            "weight_percent": stage.weight_percent,
            "timed_out": timed_out,
            "transient_error": transient_error or "",
            "snapshot": normalized,
        }
        evidence_ref = self._evidence.write_json(
            "canary-observation",
            f"{experiment_id}-stage-{stage_index}",
            payload,
        )
        control = _arm(normalized, "control")
        candidate = _arm(normalized, "candidate")
        return CanaryStageEvidence(
            experiment_id=experiment_id,
            stage_index=stage_index,
            weight_percent=stage.weight_percent,
            observed_duration_seconds=_int_value(normalized, "observed_duration_seconds"),
            total_requests=_int_value(control, "requests") + _int_value(candidate, "requests"),
            candidate_requests=_int_value(candidate, "requests"),
            control_requests=_int_value(control, "requests"),
            candidate_error_rate=_float_value(candidate, "error_rate"),
            control_error_rate=(
                _float_value(control, "error_rate")
                if _int_value(control, "requests") > 0
                else None
            ),
            candidate_p95_latency_ms=_optional_float(candidate, "p95_latency_ms") or 0.0,
            control_p95_latency_ms=(
                _optional_float(control, "p95_latency_ms")
                if _int_value(control, "requests") > 0
                else None
            ),
            evidence_refs=(evidence_ref,),
            telemetry_complete=(
                not timed_out
                and _int_value(control, "dropped_samples") == 0
                and _int_value(candidate, "dropped_samples") == 0
            ),
        )


def _snapshot_sufficient(snapshot: dict[str, Any], stage: CanaryStage) -> bool:
    normalized = _normalize_snapshot(snapshot)
    control = _arm(normalized, "control")
    candidate = _arm(normalized, "candidate")
    if _int_value(normalized, "observed_duration_seconds") < stage.min_duration_seconds:
        return False
    total = _int_value(control, "requests") + _int_value(candidate, "requests")
    if total < stage.min_requests:
        return False
    candidate_min = max(1, math.ceil(stage.min_requests * stage.weight_percent / 100))
    if _int_value(candidate, "requests") < candidate_min:
        return False
    if stage.weight_percent < 100:
        control_min = max(
            1,
            math.ceil(stage.min_requests * (100 - stage.weight_percent) / 100),
        )
        if _int_value(control, "requests") < control_min:
            return False
    return True


def _normalize_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "observed_duration_seconds": 0,
            "control": _empty_arm(),
            "candidate": _empty_arm(),
        }
    control = snapshot.get("control")
    candidate = snapshot.get("candidate")
    if not isinstance(control, dict) or not isinstance(candidate, dict):
        raise CanaryObservationError("canary metrics snapshot is missing arm data")
    return {
        **snapshot,
        "control": {str(key): value for key, value in control.items()},
        "candidate": {str(key): value for key, value in candidate.items()},
    }


def _empty_arm() -> dict[str, object]:
    return {
        "requests": 0,
        "errors": 0,
        "error_rate": 0.0,
        "p95_latency_ms": None,
        "dropped_samples": 0,
    }


def _arm(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    value = snapshot.get(name)
    if not isinstance(value, dict):
        raise CanaryObservationError(f"canary metrics snapshot has invalid {name} arm")
    return {str(key): item for key, item in value.items()}


def _int_value(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CanaryObservationError(f"canary metric {key} must be a non-negative integer")
    return value


def _float_value(document: dict[str, Any], key: str) -> float:
    value = document.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CanaryObservationError(f"canary metric {key} must be numeric")
    return float(value)


def _optional_float(document: dict[str, Any], key: str) -> float | None:
    value = document.get(key)
    if value is None:
        return None
    return _float_value(document, key)


def _require_loopback(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("V1 canary observer is restricted to local HTTP loopback")
