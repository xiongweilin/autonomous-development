from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.models import RequestAttribution
from autonomous_development.ports.persistence import RequestAttributionRepository

from .schema import request_attributions


class SqlRequestAttributionRepository(RequestAttributionRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, attribution: RequestAttribution) -> RequestAttribution:
        existing = self.get(attribution.request_ref)
        if existing is not None:
            if existing != attribution:
                raise ValueError(
                    "request reference already exists with different attribution: "
                    f"{attribution.request_ref}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(request_attributions).values(
                        request_ref=attribution.request_ref,
                        target_id=attribution.target_id,
                        observed_at=attribution.observed_at,
                        arm=attribution.arm,
                        experiment_id=attribution.experiment_id,
                        release_id=attribution.release_id,
                        deployment_id=attribution.deployment_id,
                    )
                )
        except IntegrityError:
            existing = self.get(attribution.request_ref)
            if existing is None or existing != attribution:
                raise
            return existing
        return attribution

    def get(self, request_ref: str) -> RequestAttribution | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(request_attributions).where(
                        request_attributions.c.request_ref == request_ref
                    )
                )
                .mappings()
                .first()
            )
        return _from_row(row) if row is not None else None


def _from_row(row: RowMapping) -> RequestAttribution:
    values = dict(row)
    observed_at = values["observed_at"]
    if not isinstance(observed_at, datetime):
        raise RuntimeError("persisted request attribution observed_at is not a datetime")
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return RequestAttribution(
        request_ref=str(values["request_ref"]),
        target_id=str(values["target_id"]),
        observed_at=observed_at,
        arm=str(values["arm"]),
        experiment_id=str(values["experiment_id"]),
        release_id=_optional(values.get("release_id")),
        deployment_id=_optional(values.get("deployment_id")),
    )


def _optional(value: object) -> str | None:
    return None if value is None else str(value)
