from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.adapters.prometheus import PrometheusTelemetryProvider


def provider(
    tmp_path: Path,
    handler,
) -> PrometheusTelemetryProvider:
    return PrometheusTelemetryProvider(
        "http://127.0.0.1:19090",
        {
            "error-rate": 'rate(errors_total{release="${release_id}"}[5m])',
            "latency": 'histogram_quantile(0.95, latency_bucket{target="${target_id}"})',
        },
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
        transport=httpx.MockTransport(handler),
    )


def test_prometheus_collects_content_addressed_metric_evidence(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        query = request.url.params["query"]
        assert "release-1" in query or "target-1" in query
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[1, "0.01"]]}],
                },
            },
            request=request,
        )

    now = datetime.now(UTC)
    evidence = provider(tmp_path, handler).collect(
        target_id="target-1",
        release_id="release-1",
        opened_at=now - timedelta(minutes=5),
        closed_at=now,
    )
    assert len(evidence.evidence_refs) == 2
    assert not evidence.missing_metrics
    assert all(ref.startswith("file:") for ref in evidence.evidence_refs)


def test_empty_prometheus_result_is_missing_evidence(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        result = [] if "errors_total" in query else [{"metric": {}, "values": [[1, "100"]]}]
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "matrix", "result": result}},
            request=request,
        )

    now = datetime.now(UTC)
    evidence = provider(tmp_path, handler).collect(
        target_id="target-1",
        release_id="release-1",
        opened_at=now - timedelta(minutes=5),
        closed_at=now,
    )
    assert evidence.missing_metrics == ("error-rate",)


def test_prometheus_adapter_rejects_non_loopback_endpoint(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        PrometheusTelemetryProvider(
            "https://prometheus.example.com",
            {"errors": "up"},
            LocalEvidenceStore((tmp_path / "evidence").resolve()),
        )
