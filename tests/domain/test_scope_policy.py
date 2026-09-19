import pytest

from autonomous_development.domain.models import ChangeProposal
from autonomous_development.domain.policies import ScopeViolation, validate_changed_paths


def proposal(*, max_changed_files: int = 50) -> ChangeProposal:
    return ChangeProposal(
        id="p1",
        target_id="t1",
        baseline_release_id="r1",
        baseline_commit="abc",
        objective_revision_id="o1",
        diagnosis_id=None,
        acceptance_criteria=("works",),
        allowed_paths=("src", "tests"),
        forbidden_paths=("src/secrets",),
        max_implementation_attempts=3,
        mandatory_gates=("tests",),
        max_changed_files=max_changed_files,
    )


def test_changed_paths_within_scope_are_allowed() -> None:
    validate_changed_paths(proposal(), ("src/app.py", "tests/test_app.py"))


def test_forbidden_path_wins_over_allowed_parent() -> None:
    with pytest.raises(ScopeViolation, match="explicitly forbidden"):
        validate_changed_paths(proposal(), ("src/secrets/key.txt",))


def test_path_outside_scope_is_rejected() -> None:
    with pytest.raises(ScopeViolation, match="outside allowed scope"):
        validate_changed_paths(proposal(), ("deploy/prod.yaml",))


def test_parent_escape_is_rejected() -> None:
    with pytest.raises(ScopeViolation, match="invalid repository-relative path"):
        validate_changed_paths(proposal(), ("../outside",))


def test_actual_changed_file_count_cannot_exceed_budget() -> None:
    with pytest.raises(ScopeViolation, match="exceeding budget"):
        validate_changed_paths(
            proposal(max_changed_files=1),
            ("src/app.py", "tests/test_app.py"),
        )


def test_target_contract_cannot_be_changed_autonomously() -> None:
    request = ChangeProposal(
        id="p-contract",
        target_id="t1",
        baseline_release_id="r1",
        baseline_commit="abc",
        objective_revision_id="o1",
        diagnosis_id=None,
        acceptance_criteria=("works",),
        allowed_paths=(".",),
        forbidden_paths=(),
        max_implementation_attempts=1,
        mandatory_gates=("tests",),
        max_changed_files=5,
    )
    with pytest.raises(ScopeViolation, match="system-owned"):
        validate_changed_paths(request, ("autonomous-development.toml",))
