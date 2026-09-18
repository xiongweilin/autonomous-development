from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.telemetry import TelemetryEvidence, TelemetryProvider

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class PrometheusTelemetryProvider(TelemetryProvider):
    def __init__(
        self,
        base_url: str,
        queries: Mapping[str, str],
        evidence: EvidenceStore,
        *,
        step_seconds: int = 30,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        _require_loopback(base_url)
        if not queries:
            raise ValueError("Prometheus telemetry requires at least one metric query")
        if step_seconds < 1 or timeout_seconds <= 0:
            raise ValueError("Prometheus telemetry timeouts must be positive")
        if any(not name.strip() or not query.strip() for name, query in queries.items()):
            raise ValueError("Prometheus telemetry query names and expressions must be non-empty")
        self._base_url = base_url.rstrip("/")
        self._queries = dict(queries)
        self._evidence = evidence
        self._step_seconds = step_seconds
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def collect(
        self,
        *,
        target_id: str,
        release_id: str,
        opened_at: datetime,
        closed_at: datetime,
    ) -> TelemetryEvidence:
        if closed_at < opened_at:
            raise ValueError("telemetry window closes before it opens")
        _safe_id(target_id, "target_id")
        _safe_id(release_id, "release_id")

        refs: list[str] = []
        missing: list[str] = []
        with httpx.Client(
            timeout=self._timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            for name, template in self._queries.items():
                query = (
                    template.replace("${target_id}", _promql_label(target_id))
                    .replace("${release_id}", _promql_label(release_id))
                )
                response = client.get(
                    f"{self._base_url}/api/v1/query_range",
                    params={
                        "query": query,
                        "start": opened_at.timestamp(),
                        "end": closed_at.timestamp(),
                        "step": self._step_seconds,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("status") != "success":
                    raise RuntimeError(f"Prometheus query failed for metric {name}")
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise RuntimeError(f"Prometheus response data is invalid for metric {name}")
                result = data.get("result")
                if not isinstance(result, list):
                    raise RuntimeError(f"Prometheus result is invalid for metric {name}")
                if not result:
                    missing.append(name)
                refs.append(
                    self._evidence.write_json(
                        "telemetry",
                        f"{target_id}-{release_id}-{_safe_component(name)}",
                        {
                            "target_id": target_id,
                            "release_id": release_id,
                            "metric": name,
                            "query": query,
                            "opened_at": opened_at.isoformat(),
                            "closed_at": closed_at.isoformat(),
                            "result_type": data.get("resultType"),
                            "result": result,
                        },
                    )
                )
        return TelemetryEvidence(
            evidence_refs=tuple(refs),
            missing_metrics=tuple(missing),
        )


def _require_loopback(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("V1 Prometheus telemetry is restricted to local loopback")


def _safe_id(value: str, field_name: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    if not normalized:
        raise ValueError("telemetry metric name has no safe evidence component")
    return normalized[:96]


def _promql_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
