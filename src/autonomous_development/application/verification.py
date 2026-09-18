from __future__ import annotations

from collections.abc import Iterable

from autonomous_development.domain.models import CandidateRevision, VerificationRun
from autonomous_development.ports.quality import QualityGate


class VerificationPolicyError(ValueError):
    pass


class VerificationService:
    def __init__(self, gates: Iterable[QualityGate]) -> None:
        indexed: dict[str, QualityGate] = {}
        for gate in gates:
            if gate.gate_id in indexed:
                raise VerificationPolicyError(f"duplicate quality gate: {gate.gate_id}")
            indexed[gate.gate_id] = gate
        self._gates = indexed

    def run(
        self,
        candidate: CandidateRevision,
        *,
        run_id: str,
        required_gates: tuple[str, ...],
    ) -> VerificationRun:
        if not run_id.strip():
            raise VerificationPolicyError("verification run id must be non-empty")
        if not required_gates:
            raise VerificationPolicyError("verification requires at least one gate")
        if len(set(required_gates)) != len(required_gates):
            raise VerificationPolicyError("required gate list contains duplicates")

        missing = tuple(gate for gate in required_gates if gate not in self._gates)
        if missing:
            raise VerificationPolicyError(
                "required quality gates are not configured: " + ", ".join(missing)
            )

        checks = tuple(self._gates[gate].evaluate(candidate) for gate in required_gates)
        return VerificationRun(id=run_id, candidate_id=candidate.id, checks=checks)
