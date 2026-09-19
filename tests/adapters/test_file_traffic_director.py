from pathlib import Path

import pytest

from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.adapters.traffic import AtomicFileTrafficDirector
from autonomous_development.adapters.traffic.file import TrafficOperationConflict
from autonomous_development.ports.traffic import TrafficSplit


def split(
    *,
    operation_id: str = "canary:experiment-1:stage:0",
    weight: int = 10,
) -> TrafficSplit:
    return TrafficSplit(
        experiment_id="experiment-1",
        stage_index=0,
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
        candidate_weight_percent=weight,
        operation_id=operation_id,
        target_id="target-1",
        control_release_id="release-1",
        candidate_deployment_id="deployment-2",
    )


def director(tmp_path: Path) -> AtomicFileTrafficDirector:
    return AtomicFileTrafficDirector(
        (tmp_path / "traffic").resolve(),
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )


def test_same_operation_replays_without_new_generation(tmp_path: Path) -> None:
    traffic = director(tmp_path)
    first = traffic.apply(split())
    replay = traffic.apply(split())
    assert replay == first
    assert first.generation == 1


def test_same_operation_cannot_change_split(tmp_path: Path) -> None:
    traffic = director(tmp_path)
    traffic.apply(split())
    with pytest.raises(TrafficOperationConflict):
        traffic.apply(split(weight=20))


def test_new_operation_advances_generation_and_restore_is_explicit(tmp_path: Path) -> None:
    traffic = director(tmp_path)
    first = traffic.apply(split())
    second = traffic.apply(split(operation_id="canary:experiment-1:stage:0:retry", weight=20))
    restored = traffic.restore_control(
        experiment_id="experiment-1",
        stage_index=0,
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
        operation_id="canary:experiment-1:stage:0:rollback",
    )
    assert (first.generation, second.generation, restored.generation) == (1, 2, 3)
    assert restored.candidate_weight_percent == 0
    current = traffic.read_current()
    assert current is not None
    assert current.candidate_weight_percent == 0
    assert current.target_id == "target-1"
    assert current.control_release_id == "release-1"
    assert current.candidate_deployment_id == "deployment-2"


def test_non_loopback_routes_are_rejected(tmp_path: Path) -> None:
    traffic = director(tmp_path)
    invalid = TrafficSplit(
        experiment_id="experiment-1",
        stage_index=0,
        control_base_url="https://prod.example.com",
        candidate_base_url="http://127.0.0.1:4200",
        candidate_weight_percent=10,
        operation_id="op-1",
    )
    with pytest.raises(ValueError, match="loopback"):
        traffic.apply(invalid)
