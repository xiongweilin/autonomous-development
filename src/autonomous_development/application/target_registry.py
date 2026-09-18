from __future__ import annotations

from dataclasses import replace

from autonomous_development.domain.models import (
    DevelopmentTarget,
    ProductObjectiveRevision,
)
from autonomous_development.ports.persistence import ObjectiveRepository, TargetRepository


class TargetNotFoundError(LookupError):
    pass


class ObjectiveNotFoundError(LookupError):
    pass


class TargetRegistryService:
    def __init__(
        self,
        targets: TargetRepository,
        objectives: ObjectiveRepository,
    ) -> None:
        self._targets = targets
        self._objectives = objectives

    def register(
        self,
        target: DevelopmentTarget,
        objective: ProductObjectiveRevision,
    ) -> DevelopmentTarget:
        if objective.target_id != target.id:
            raise ValueError("objective does not belong to target")
        if objective.id != target.active_objective_revision_id:
            raise ValueError("target active objective does not match supplied objective")
        if objective.mutation_policy != target.mutation_policy:
            raise ValueError("target and objective mutation policies must match")
        self._objectives.add(objective)
        return self._targets.add(target)

    def get_target(self, target_id: str) -> DevelopmentTarget:
        target = self._targets.get(target_id)
        if target is None:
            raise TargetNotFoundError(target_id)
        return target

    def list_targets(self) -> tuple[DevelopmentTarget, ...]:
        return self._targets.list_all()

    def get_active_objective(self, target: DevelopmentTarget) -> ProductObjectiveRevision:
        objective = self._objectives.get(target.active_objective_revision_id)
        if objective is None:
            raise ObjectiveNotFoundError(target.active_objective_revision_id)
        if objective.target_id != target.id:
            raise RuntimeError("active objective target identity is inconsistent")
        if objective.mutation_policy != target.mutation_policy:
            raise RuntimeError("active objective mutation policy is inconsistent")
        return objective

    def runtime_target(
        self,
        target_id: str,
        *,
        serving_release_id: str | None,
    ) -> DevelopmentTarget:
        target = self.get_target(target_id)
        return replace(target, current_release_id=serving_release_id)
