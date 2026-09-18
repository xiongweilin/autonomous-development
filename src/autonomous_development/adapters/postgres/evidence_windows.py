from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.models import EvidenceWindow
from autonomous_development.ports.persistence import EvidenceWindowRepository

from .schema import evidence_windows


class SqlEvidenceWindowRepository(EvidenceWindowRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, window: EvidenceWindow) -> EvidenceWindow:
        existing = self.get(window.id)
        if existing is not None:
            if existing != window:
                raise ValueError(
                    f"evidence window id already exists with different content: {window.id}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(evidence_windows).values(
                        id=window.id,
                        target_id=window.target_id,
                        release_ids_json=list(window.release_ids),
                        opened_at=window.opened_at,
                        closed_at=window.closed_at,
                        telemetry_refs_json=list(window.telemetry_refs),
                        feedback_refs_json=list(window.feedback_refs),
                        regression_refs_json=list(window.regression_refs),
                        incident_refs_json=list(window.incident_refs),
                        missing_evidence_json=list(window.missing_evidence),
                    )
                )
        except IntegrityError:
            existing = self.get(window.id)
            if existing is None or existing != window:
                raise
            return existing
        return window

    def get(self, window_id: str) -> EvidenceWindow | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(evidence_windows).where(evidence_windows.c.id == window_id)
                )
                .mappings()
                .first()
            )
        return _window_from_row(row) if row is not None else None


def _window_from_row(row: RowMapping) -> EvidenceWindow:
    values = dict(row)
    return EvidenceWindow(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        release_ids=_strings(values["release_ids_json"], "release_ids"),
        opened_at=_utc(values["opened_at"], "opened_at"),
        closed_at=_utc(values["closed_at"], "closed_at"),
        telemetry_refs=_strings(values["telemetry_refs_json"], "telemetry_refs"),
        feedback_refs=_strings(values["feedback_refs_json"], "feedback_refs"),
        regression_refs=_strings(values["regression_refs_json"], "regression_refs"),
        incident_refs=_strings(values["incident_refs_json"], "incident_refs"),
        missing_evidence=_strings(values["missing_evidence_json"], "missing_evidence"),
    )


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"persisted evidence window {field_name} is malformed")
    return tuple(str(item) for item in value)


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError(f"persisted evidence window {field_name} is not a datetime")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
