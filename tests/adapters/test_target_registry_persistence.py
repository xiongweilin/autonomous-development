from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.adapters.postgres.target_registry import (
    SqlObjectiveRepository,
    SqlTargetRepository,
)
from autonomous_development.domain.models import (
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
)


def policy() -> MutationPolicy:
    return MutationPolicy(
        allowed_paths=("src", "tests"),
        forbidden_paths=("deploy",),
        max_changed_files=5,
        max_implementation_attempts=2,
    )


def target() -> DevelopmentTarget:
    return DevelopmentTarget(
        id="target-1",
        repository="/repo",
        default_branch="main",
        target_contract_revision="contract-1",
        active_objective_revision_id="objective-1",
        mutation_policy=policy(),
    )


def objective() -> ProductObjectiveRevision:
    return ProductObjectiveRevision(
        id="objective-1",
        target_id="target-1",
        statement="Improve correctness without reliability regression.",
        acceptance_criteria=("seeded defect is fixed",),
        primary_metrics=("correctness",),
        reliability_constraints=("error rate must not regress",),
        performance_constraints=("p95 remains within target",),
        security_constraints=("no secret exposure",),
        mutation_policy=policy(),
        created_at=datetime.now(UTC),
    )


def repositories() -> tuple[SqlTargetRepository, SqlObjectiveRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return SqlTargetRepository(engine), SqlObjectiveRepository(engine)


def test_target_and_objective_roundtrip_idempotently() -> None:
    targets, objectives = repositories()
    expected_target = target()
    expected_objective = objective()

    assert objectives.add(expected_objective) == expected_objective
    assert targets.add(expected_target) == expected_target
    assert objectives.get("objective-1") == expected_objective
    assert targets.get("target-1") == expected_target
    assert targets.list_all() == (expected_target,)

    assert objectives.add(expected_objective) == expected_objective
    assert targets.add(expected_target) == expected_target


def test_objective_revision_id_is_immutable() -> None:
    _, objectives = repositories()
    original = objective()
    objectives.add(original)
    changed = ProductObjectiveRevision(
        id=original.id,
        target_id=original.target_id,
        statement="Different statement",
        acceptance_criteria=original.acceptance_criteria,
        primary_metrics=original.primary_metrics,
        reliability_constraints=original.reliability_constraints,
        performance_constraints=original.performance_constraints,
        security_constraints=original.security_constraints,
        mutation_policy=original.mutation_policy,
        created_at=original.created_at,
    )
    with pytest.raises(ValueError, match="different content"):
        objectives.add(changed)
