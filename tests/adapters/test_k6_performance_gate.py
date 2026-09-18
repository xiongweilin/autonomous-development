import json
from pathlib import Path

from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.adapters.quality import K6PerformanceGate
from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision
from autonomous_development.ports.process import CommandRequest, CommandResult


class FakeK6Runner:
    def __init__(self, *, returncode: int = 0, include_thresholds: bool = True) -> None:
        self.returncode = returncode
        self.include_thresholds = include_thresholds
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        summary_path = Path(request.command[request.command.index("--summary-export") + 1])
        threshold_payload = {"p(95)<200": {"ok": self.returncode == 0}}
        metrics = {
            "http_req_duration": {
                "type": "trend",
                "values": {"p(95)": 120.0},
                "thresholds": threshold_payload if self.include_thresholds else {},
            },
            "http_req_failed": {
                "type": "rate",
                "values": {"rate": 0.0},
                "thresholds": (
                    {"rate<0.01": {"ok": self.returncode == 0}}
                    if self.include_thresholds
                    else {}
                ),
            },
        }
        summary_path.write_text(json.dumps({"metrics": metrics}), encoding="utf-8")
        return CommandResult(self.returncode, "k6 output", "")


def candidate(tmp_path: Path) -> CandidateRevision:
    script = tmp_path / "tests" / "performance" / "smoke.js"
    script.parent.mkdir(parents=True)
    script.write_text("export default function() {}\n", encoding="utf-8")
    return CandidateRevision(
        id="candidate-k6",
        cycle_id="cycle-1",
        worktree_path=str(tmp_path.resolve()),
        branch_name="autodev/cycle-1",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        tree_hash="c" * 40,
        changed_paths=("tests/performance/smoke.js",),
        codex_thread_id="thread-1",
        implementation_attempt=1,
    )


def _gate(tmp_path: Path, runner: FakeK6Runner) -> K6PerformanceGate:
    return K6PerformanceGate(
        runner=runner,
        evidence=LocalEvidenceStore((tmp_path / "evidence").resolve()),
        base_url="http://127.0.0.1:49155",
        script_path="tests/performance/smoke.js",
        required_threshold_metrics=("http_req_failed", "http_req_duration"),
        timeout_seconds=30,
    )


def test_k6_gate_passes_only_with_required_thresholds(tmp_path: Path) -> None:
    runner = FakeK6Runner()
    check = _gate(tmp_path, runner).evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.PASSED
    assert runner.requests[0].environment["AUTODEV_BASE_URL"] == "http://127.0.0.1:49155"


def test_k6_gate_blocks_vacuous_script_without_thresholds(tmp_path: Path) -> None:
    check = _gate(tmp_path, FakeK6Runner(include_thresholds=False)).evaluate(
        candidate(tmp_path)
    )
    assert check.status is VerificationStatus.BLOCKED


def test_k6_threshold_failure_is_failed_not_blocked(tmp_path: Path) -> None:
    check = _gate(tmp_path, FakeK6Runner(returncode=99)).evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.FAILED
