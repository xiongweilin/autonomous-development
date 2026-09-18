from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision, VerificationCheck
from autonomous_development.ports.codex import (
    CodexProvider,
    CodexProviderError,
    CodexSandbox,
    CodexTurnRequest,
)
from autonomous_development.ports.evidence import EvidenceStore


class ReviewResult(TypedDict):
    summary: str
    blocking_findings: list[str]


_REVIEW_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "blocking_findings"],
    "properties": {
        "summary": {"type": "string"},
        "blocking_findings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


class CodexReviewGate:
    def __init__(
        self,
        codex: CodexProvider,
        evidence: EvidenceStore,
        *,
        gate_id: str = "codex-review",
        timeout_seconds: int = 1200,
    ) -> None:
        self._codex = codex
        self._evidence = evidence
        self._gate_id = gate_id
        self._timeout_seconds = timeout_seconds

    @property
    def gate_id(self) -> str:
        return self._gate_id

    def evaluate(self, candidate: CandidateRevision) -> VerificationCheck:
        started = datetime.now(UTC)
        status = VerificationStatus.BLOCKED
        blocking_findings: tuple[str, ...] = ()
        payload: dict[str, object] = {
            "gate": self._gate_id,
            "candidate_id": candidate.id,
            "candidate_commit": candidate.candidate_commit,
            "base_commit": candidate.base_commit,
            "changed_paths": list(candidate.changed_paths),
        }
        try:
            result = self._codex.run_turn(
                CodexTurnRequest(
                    prompt=_review_prompt(candidate),
                    cwd=Path(candidate.worktree_path),
                    sandbox=CodexSandbox.READ_ONLY,
                    output_schema=_REVIEW_SCHEMA,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            if not result.completed:
                raise CodexProviderError(f"review turn ended with status {result.status}")
            review = _parse_review(result.agent_messages)
            blocking_findings = tuple(review["blocking_findings"])
            status = (
                VerificationStatus.FAILED
                if blocking_findings
                else VerificationStatus.PASSED
            )
            payload.update(
                {
                    "thread_id": result.thread_id,
                    "turn_id": result.turn_id,
                    "summary": review["summary"],
                    "blocking_findings": list(blocking_findings),
                }
            )
        except (CodexProviderError, ValueError, json.JSONDecodeError) as exc:
            payload.update({"error_type": type(exc).__name__, "error": str(exc)})
        ended = datetime.now(UTC)
        payload.update({"status": status.value, "ended_at": ended.isoformat()})
        evidence_ref = self._evidence.write_json(
            "quality",
            f"{candidate.id}-{self._gate_id}",
            payload,
        )
        return VerificationCheck(
            id=f"{candidate.id}:{self._gate_id}",
            gate=self._gate_id,
            status=status,
            started_at=started,
            ended_at=ended,
            evidence_refs=(evidence_ref,),
            measurements={"blocking_findings": len(blocking_findings)},
        )


def _parse_review(messages: tuple[str, ...]) -> ReviewResult:
    if not messages:
        raise ValueError("Codex review produced no agent message")
    parsed = json.loads(messages[-1])
    if not isinstance(parsed, dict):
        raise ValueError("Codex review output must be a JSON object")
    summary = parsed.get("summary")
    findings = parsed.get("blocking_findings")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Codex review summary must be non-empty")
    if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
        raise ValueError("Codex review blocking_findings must be a string array")
    return {"summary": summary, "blocking_findings": findings}


def _review_prompt(candidate: CandidateRevision) -> str:
    paths = "\n".join(f"- {path}" for path in candidate.changed_paths)
    return f"""Review candidate commit {candidate.candidate_commit} against base
{candidate.base_commit}.

Prioritize correctness, contracts, state transitions, failure paths,
maintainability and regression risk. Do not edit files. Do not suggest expanding
the authorized product scope merely to make the change easier.

Changed paths:
{paths}

Return only the requested structured review object. A blocking finding means the
candidate should not be promoted until it is fixed.
"""
