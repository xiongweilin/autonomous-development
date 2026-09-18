from datetime import UTC, datetime

import pytest

from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.models import (
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
)


class Targets:
    def __init__(self) -> None:
        self.items: dict[str, DevelopmentTarget] = {}

    def add(self, target: DevelopmentTarget) -> DevelopmentTarget:
        existing = self.items.get(target.id)
        if existing is not None and existing != target:
            raise ValueError("target conflict")
        self.items[target.id] = target
        return target

    def get(self, target_id: str) -> DevelopmentTarget | None:
        return self.items.get(target_id)

    def list_all(self) -> tuple[DevelopmentTarget, ...]:
        return tuple(self.items[key] for key in sorted(self.items))


class Objectives:
    def __init__(self) -> None:
        self.items: dict[str, ProductObjectiveRevision] = {}

    def add(self, objective: ProductObjectiveRevision) -> ProductObjectiveRevision:
        existing = self.items.get(objective.id)
        if existing is not None and existing != objective:
            raise ValueError("objective conflict")
        self.items[objective.id] = objective
        return objective

    def get(self, objective_id: str) -> ProductObjectiveRevision | None:
        return self.items.get(objective_id)


def policy() -> MutationPolicy:
    return MutationPolicy(allowed_paths=("src",), max_changed_files=3)


def objective() -> ProductObjectiveRevision:
    return ProductObjectiveRevision(
        id="objective-1",
        target_id="target-1",
        statement="Fix user-visible defects.",
        acceptance_criteria=("reported defect is fixed",),
        primary_metrics=("correctness",),
        reliability_constraints=(),
        performance_constraints=(),
        security_constraints=(),
        mutation_policy=policy(),
        created_at=datetime.now(UTC),
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


def test_registry_requires_target_objective_policy_identity() -> None:
    service = TargetRegistryService(Targets(), Objectives())
    service.register(target(), objective())

    registered = service.get_target("target-1")
    active = service.get_active_objective(registered)
    runtime = service.runtime_target("target-1", serving_release_id="release-1")

    assert active.id == "objective-1"
    assert runtime.current_release_id == "release-1"


def test_registry_rejects_mismatched_objective() -> None:
    service = TargetRegistryService(Targets(), Objectives())
    wrong = ProductObjectiveRevision(
        id="objective-2",
        target_id="other-target",
        statement="wrong",
        acceptance_criteria=("wrong",),
        primary_metrics=(),
        reliability_constraints=(),
        performance_constraints=(),
        security_constraints=(),
        mutation_policy=policy(),
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="does not belong"):
        service.register(target(), wrong)
