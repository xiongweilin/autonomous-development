from __future__ import annotations

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.models import Diagnosis
from autonomous_development.ports.persistence import DiagnosisRepository

from .schema import diagnoses


class SqlDiagnosisRepository(DiagnosisRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, diagnosis: Diagnosis) -> Diagnosis:
        existing = self.get(diagnosis.id)
        if existing is not None:
            if existing != diagnosis:
                raise ValueError(
                    f"diagnosis id already exists with different content: {diagnosis.id}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(diagnoses).values(
                        id=diagnosis.id,
                        evidence_window_id=diagnosis.evidence_window_id,
                        observed_problem=diagnosis.observed_problem,
                        affected_journey=diagnosis.affected_journey,
                        evidence_refs_json=list(diagnosis.evidence_refs),
                        confidence=diagnosis.confidence,
                        competing_hypotheses_json=list(diagnosis.competing_hypotheses),
                        likely_root_cause=diagnosis.likely_root_cause,
                        proposed_change_class=diagnosis.proposed_change_class,
                        expected_outcome=diagnosis.expected_outcome,
                        risks_json=list(diagnosis.risks),
                        requested_paths_json=list(diagnosis.requested_paths),
                        required_validation_json=list(diagnosis.required_validation),
                    )
                )
        except IntegrityError:
            existing = self.get(diagnosis.id)
            if existing is None or existing != diagnosis:
                raise
            return existing
        return diagnosis

    def get(self, diagnosis_id: str) -> Diagnosis | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(diagnoses).where(diagnoses.c.id == diagnosis_id))
                .mappings()
                .first()
            )
        return _diagnosis_from_row(row) if row is not None else None


def _diagnosis_from_row(row: RowMapping) -> Diagnosis:
    values = dict(row)
    return Diagnosis(
        id=str(values["id"]),
        evidence_window_id=str(values["evidence_window_id"]),
        observed_problem=str(values["observed_problem"]),
        affected_journey=str(values["affected_journey"]),
        evidence_refs=_strings(values["evidence_refs_json"], "evidence_refs"),
        confidence=float(values["confidence"]),
        competing_hypotheses=_strings(
            values["competing_hypotheses_json"],
            "competing_hypotheses",
        ),
        likely_root_cause=str(values["likely_root_cause"]),
        proposed_change_class=str(values["proposed_change_class"]),
        expected_outcome=str(values["expected_outcome"]),
        risks=_strings(values["risks_json"], "risks"),
        requested_paths=_strings(values["requested_paths_json"], "requested_paths"),
        required_validation=_strings(
            values["required_validation_json"],
            "required_validation",
        ),
    )


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"persisted diagnosis {field_name} is malformed")
    return tuple(str(item) for item in value)
