from __future__ import annotations

from typing import Protocol

from autonomous_development.domain.models import CandidateRevision, VerificationCheck


class QualityGate(Protocol):
    @property
    def gate_id(self) -> str: ...

    def evaluate(self, candidate: CandidateRevision) -> VerificationCheck: ...
