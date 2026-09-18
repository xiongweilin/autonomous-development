from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.enums import FeedbackKind
from autonomous_development.domain.models import UserFeedback
from autonomous_development.ports.persistence import FeedbackRepository

from .schema import user_feedback


class SqlFeedbackRepository(FeedbackRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, feedback: UserFeedback) -> UserFeedback:
        existing = self.get(feedback.id)
        if existing is not None:
            if existing != feedback:
                raise ValueError(
                    f"feedback id already exists with different content: {feedback.id}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(user_feedback).values(**_feedback_values(feedback)))
        except IntegrityError:
            existing = self.get(feedback.id)
            if existing is None or existing != feedback:
                raise
            return existing
        return feedback

    def get(self, feedback_id: str) -> UserFeedback | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(user_feedback).where(user_feedback.c.id == feedback_id)
                )
                .mappings()
                .first()
            )
        return _feedback_from_row(row) if row is not None else None

    def list_attributable(
        self,
        target_id: str,
        release_id: str,
        *,
        opened_at: datetime,
        closed_at: datetime,
    ) -> tuple[UserFeedback, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(user_feedback)
                    .where(
                        user_feedback.c.target_id == target_id,
                        user_feedback.c.release_id == release_id,
                        user_feedback.c.received_at >= opened_at,
                        user_feedback.c.received_at <= closed_at,
                    )
                    .order_by(user_feedback.c.received_at, user_feedback.c.id)
                )
                .mappings()
                .all()
            )
        return tuple(_feedback_from_row(row) for row in rows)


def _feedback_values(feedback: UserFeedback) -> dict[str, object]:
    return {
        "id": feedback.id,
        "target_id": feedback.target_id,
        "received_at": feedback.received_at,
        "kind": feedback.kind.value,
        "category": feedback.category,
        "severity": feedback.severity,
        "provenance": feedback.provenance,
        "release_id": feedback.release_id,
        "deployment_id": feedback.deployment_id,
        "experiment_id": feedback.experiment_id,
        "request_ref": feedback.request_ref,
        "free_text": feedback.free_text,
    }


def _feedback_from_row(row: RowMapping) -> UserFeedback:
    values = dict(row)
    return UserFeedback(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        received_at=_utc_datetime(values["received_at"]),
        kind=FeedbackKind(str(values["kind"])),
        category=str(values["category"]),
        severity=int(values["severity"]),
        provenance=str(values["provenance"]),
        release_id=_optional_str(values.get("release_id")),
        deployment_id=_optional_str(values.get("deployment_id")),
        experiment_id=_optional_str(values.get("experiment_id")),
        request_ref=_optional_str(values.get("request_ref")),
        free_text=_optional_str(values.get("free_text")),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)



def _utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("persisted feedback received_at is not a datetime")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
