from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.diagnoses import SqlDiagnosisRepository
from autonomous_development.adapters.postgres.proposals import SqlChangeProposalRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.domain.models import ChangeProposal, Diagnosis


def test_diagnosis_and_proposal_are_immutable_roundtrip_records() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    diagnoses = SqlDiagnosisRepository(engine)
    proposals = SqlChangeProposalRepository(engine)

    diagnosis = Diagnosis(
        id="diagnosis-1",
        evidence_window_id="window-1",
        observed_problem="wrong answer",
        affected_journey="answer generation",
        evidence_refs=("feedback:1", "telemetry:1"),
        confidence=0.8,
        competing_hypotheses=("parser issue",),
        likely_root_cause="off-by-one",
        proposed_change_class="bugfix",
        expected_outcome="correct answer",
        risks=("parser regression",),
        requested_paths=("src/app.py",),
        required_validation=("regression test",),
    )
    proposal = ChangeProposal(
        id="proposal-1",
        target_id="target-1",
        baseline_release_id="release-1",
        baseline_commit="a" * 40,
        objective_revision_id="objective-1",
        diagnosis_id="diagnosis-1",
        acceptance_criteria=("known defect is fixed",),
        allowed_paths=("src/app.py",),
        forbidden_paths=("deploy",),
        max_implementation_attempts=2,
        mandatory_gates=("tests",),
        change_intent="Fix the diagnosed wrong-answer defect.",
    )

    assert diagnoses.add(diagnosis) == diagnosis
    assert diagnoses.get("diagnosis-1") == diagnosis
    assert diagnoses.add(diagnosis) == diagnosis
    assert proposals.add(proposal) == proposal
    assert proposals.get("proposal-1") == proposal
    assert proposals.add(proposal) == proposal
