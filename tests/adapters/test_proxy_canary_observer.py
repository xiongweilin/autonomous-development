from pathlib import Path

import httpx

from autonomous_development.adapters.canary_proxy import ProxyCanaryObserver
from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.domain.canary import CanaryGuardrails, evaluate_canary_stage
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import CanaryStage, Experiment
from autonomous_development.ports.traffic import TrafficRouteState


def stage() -> CanaryStage:
    return CanaryStage(weight_percent=10, min_duration_seconds=60, min_requests=100)


def route_state() -> TrafficRouteState:
    return TrafficRouteState(
        experiment_id="experiment-1",
        stage_index=0,
        candidate_weight_percent=10,
        generation=7,
        evidence_ref="traffic:7",
    )


def snapshot(*, dropped: int = 0) -> dict[str, object]:
    return {
        "generation": 7,
        "experiment_id": "experiment-1",
        "stage_index": 0,
        "observed_duration_seconds": 60,
        "control": {
            "requests": 90,
            "errors": 0,
            "error_rate": 0.0,
            "p95_latency_ms": 100.0,
            "dropped_samples": 0,
        },
        "candidate": {
            "requests": 10,
            "errors": 0,
            "error_rate": 0.0,
            "p95_latency_ms": 110.0,
            "dropped_samples": dropped,
        },
    }


def observer(tmp_path: Path, *, dropped: int = 0) -> ProxyCanaryObserver:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot(dropped=dropped), request=request)

    return ProxyCanaryObserver(
        "http://127.0.0.1:4300",
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
        observation_timeout_seconds=1,
        poll_interval_seconds=0.001,
        transport=httpx.MockTransport(handler),
    )


def test_observer_converts_proxy_metrics_to_stage_evidence(tmp_path: Path) -> None:
    evidence = observer(tmp_path).observe(
        experiment_id="experiment-1",
        stage_index=0,
        stage=stage(),
        route_state=route_state(),
    )
    assert evidence.total_requests == 100
    assert evidence.candidate_requests == 10
    assert evidence.control_requests == 90
    assert evidence.telemetry_complete
    assert evidence.evidence_refs[0].startswith("file:")


def test_dropped_latency_samples_hold_instead_of_promoting(tmp_path: Path) -> None:
    evidence = observer(tmp_path, dropped=1).observe(
        experiment_id="experiment-1",
        stage_index=0,
        stage=stage(),
        route_state=route_state(),
    )
    decision = evaluate_canary_stage(
        Experiment(
            id="experiment-1",
            target_id="target-1",
            control_release_id="release-1",
            candidate_deployment_id="deployment-1",
            stages=(stage(), CanaryStage(100, 120, 200)),
        ),
        evidence,
        CanaryGuardrails(0.02, 0.01, 250.0, 1.25),
    )
    assert decision.kind is CanaryDecisionKind.HOLD
    assert "telemetry_incomplete" in decision.reason
