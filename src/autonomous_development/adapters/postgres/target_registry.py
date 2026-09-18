from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.models import (
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
)
from autonomous_development.ports.persistence import ObjectiveRepository, TargetRepository

from .schema import development_targets, product_objective_revisions


class SqlTargetRepository(TargetRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, target: DevelopmentTarget) -> DevelopmentTarget:
        existing = self.get(target.id)
        if existing is not None:
            if existing != target:
                raise ValueError(
                    f"target id already exists with different content: {target.id}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(development_targets).values(**_target_values(target))
                )
        except IntegrityError:
            existing = self.get(target.id)
            if existing is None or existing != target:
                raise
            return existing
        return target

    def get(self, target_id: str) -> DevelopmentTarget | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(development_targets).where(
                        development_targets.c.id == target_id
                    )
                )
                .mappings()
                .first()
            )
        return _target_from_row(row) if row is not None else None

    def list_all(self) -> tuple[DevelopmentTarget, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(development_targets).order_by(development_targets.c.id)
                )
                .mappings()
                .all()
            )
        return tuple(_target_from_row(row) for row in rows)


class SqlObjectiveRepository(ObjectiveRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, objective: ProductObjectiveRevision) -> ProductObjectiveRevision:
        existing = self.get(objective.id)
        if existing is not None:
            if existing != objective:
                raise ValueError(
                    f"objective id already exists with different content: {objective.id}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(product_objective_revisions).values(
                        **_objective_values(objective)
                    )
                )
        except IntegrityError:
            existing = self.get(objective.id)
            if existing is None or existing != objective:
                raise
            return existing
        return objective

    def get(self, objective_id: str) -> ProductObjectiveRevision | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(product_objective_revisions).where(
                        product_objective_revisions.c.id == objective_id
                    )
                )
                .mappings()
                .first()
            )
        return _objective_from_row(row) if row is not None else None


def _target_values(target: DevelopmentTarget) -> dict[str, object]:
    policy = target.mutation_policy
    return {
        "id": target.id,
        "repository": target.repository,
        "default_branch": target.default_branch,
        "target_contract_revision": target.target_contract_revision,
        "active_objective_revision_id": target.active_objective_revision_id,
        "allowed_paths_json": list(policy.allowed_paths),
        "forbidden_paths_json": list(policy.forbidden_paths),
        "max_changed_files": policy.max_changed_files,
        "max_implementation_attempts": policy.max_implementation_attempts,
        "current_release_id": target.current_release_id,
    }


def _objective_values(objective: ProductObjectiveRevision) -> dict[str, object]:
    policy = objective.mutation_policy
    return {
        "id": objective.id,
        "target_id": objective.target_id,
        "statement": objective.statement,
        "acceptance_criteria_json": list(objective.acceptance_criteria),
        "primary_metrics_json": list(objective.primary_metrics),
        "reliability_constraints_json": list(objective.reliability_constraints),
        "performance_constraints_json": list(objective.performance_constraints),
        "security_constraints_json": list(objective.security_constraints),
        "allowed_paths_json": list(policy.allowed_paths),
        "forbidden_paths_json": list(policy.forbidden_paths),
        "max_changed_files": policy.max_changed_files,
        "max_implementation_attempts": policy.max_implementation_attempts,
        "created_at": objective.created_at,
    }


def _target_from_row(row: RowMapping) -> DevelopmentTarget:
    values = dict(row)
    return DevelopmentTarget(
        id=str(values["id"]),
        repository=str(values["repository"]),
        default_branch=str(values["default_branch"]),
        target_contract_revision=str(values["target_contract_revision"]),
        active_objective_revision_id=str(values["active_objective_revision_id"]),
        mutation_policy=_policy(values),
        current_release_id=_optional_str(values.get("current_release_id")),
    )


def _objective_from_row(row: RowMapping) -> ProductObjectiveRevision:
    values = dict(row)
    return ProductObjectiveRevision(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        statement=str(values["statement"]),
        acceptance_criteria=_strings(
            values["acceptance_criteria_json"], "acceptance_criteria"
        ),
        primary_metrics=_strings(values["primary_metrics_json"], "primary_metrics"),
        reliability_constraints=_strings(
            values["reliability_constraints_json"], "reliability_constraints"
        ),
        performance_constraints=_strings(
            values["performance_constraints_json"], "performance_constraints"
        ),
        security_constraints=_strings(
            values["security_constraints_json"], "security_constraints"
        ),
        mutation_policy=_policy(values),
        created_at=_utc(values["created_at"], "created_at"),
    )


def _policy(values: dict[str, object]) -> MutationPolicy:
    return MutationPolicy(
        allowed_paths=_strings(values["allowed_paths_json"], "allowed_paths"),
        forbidden_paths=_strings(values["forbidden_paths_json"], "forbidden_paths"),
        max_changed_files=_integer(values["max_changed_files"], "max_changed_files"),
        max_implementation_attempts=_integer(
            values["max_implementation_attempts"],
            "max_implementation_attempts",
        ),
    )


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"persisted registry {field} is malformed")
    return tuple(str(item) for item in value)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError(f"persisted registry {field} is not a datetime")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)



def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"persisted registry {field} is not an integer")
    return value



def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"persisted registry {field} is not an integer")
    return value



def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"persisted registry {field} is not an integer")
    return value
