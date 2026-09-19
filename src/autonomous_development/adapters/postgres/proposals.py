from __future__ import annotations

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.models import ChangeProposal
from autonomous_development.ports.persistence import ChangeProposalRepository

from .schema import change_proposals


class SqlChangeProposalRepository(ChangeProposalRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, proposal: ChangeProposal) -> ChangeProposal:
        existing = self.get(proposal.id)
        if existing is not None:
            if existing != proposal:
                raise ValueError(
                    f"proposal id already exists with different content: {proposal.id}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(change_proposals).values(
                        id=proposal.id,
                        target_id=proposal.target_id,
                        baseline_release_id=proposal.baseline_release_id,
                        baseline_commit=proposal.baseline_commit,
                        objective_revision_id=proposal.objective_revision_id,
                        diagnosis_id=proposal.diagnosis_id,
                        acceptance_criteria_json=list(proposal.acceptance_criteria),
                        allowed_paths_json=list(proposal.allowed_paths),
                        forbidden_paths_json=list(proposal.forbidden_paths),
                        max_implementation_attempts=proposal.max_implementation_attempts,
                        max_changed_files=proposal.max_changed_files,
                        mandatory_gates_json=list(proposal.mandatory_gates),
                        change_intent=proposal.change_intent,
                    )
                )
        except IntegrityError:
            existing = self.get(proposal.id)
            if existing is None or existing != proposal:
                raise
            return existing
        return proposal

    def get(self, proposal_id: str) -> ChangeProposal | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(change_proposals).where(change_proposals.c.id == proposal_id)
                )
                .mappings()
                .first()
            )
        return _proposal_from_row(row) if row is not None else None


def _proposal_from_row(row: RowMapping) -> ChangeProposal:
    values = dict(row)
    return ChangeProposal(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        baseline_release_id=str(values["baseline_release_id"]),
        baseline_commit=str(values["baseline_commit"]),
        objective_revision_id=str(values["objective_revision_id"]),
        diagnosis_id=_optional_str(values.get("diagnosis_id")),
        acceptance_criteria=_strings(
            values["acceptance_criteria_json"],
            "acceptance_criteria",
        ),
        allowed_paths=_strings(values["allowed_paths_json"], "allowed_paths"),
        forbidden_paths=_strings(values["forbidden_paths_json"], "forbidden_paths"),
        max_implementation_attempts=int(values["max_implementation_attempts"]),
        mandatory_gates=_strings(values["mandatory_gates_json"], "mandatory_gates"),
        change_intent=_optional_str(values.get("change_intent")),
        max_changed_files=int(values["max_changed_files"]),
    )


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"persisted change proposal {field_name} is malformed")
    return tuple(str(item) for item in value)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
