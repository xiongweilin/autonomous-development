from pathlib import Path

from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.adapters.supply_chain import SyftGrypeScanner
from autonomous_development.ports.process import CommandRequest, CommandResult


class FakeScanRunner:
    def __init__(self, grype_returncode: int) -> None:
        self.grype_returncode = grype_returncode
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        if request.command[0] == "syft":
            output_arg = request.command[request.command.index("-o") + 1]
            output_path = Path(output_arg.split("=", 1)[1])
            output_path.write_text(
                '{"bomFormat":"CycloneDX","components":[]}',
                encoding="utf-8",
            )
            return CommandResult(returncode=0, stdout="", stderr="")
        if request.command[0] == "grype":
            output_path = Path(request.command[request.command.index("--file") + 1])
            output_path.write_text('{"matches":[]}', encoding="utf-8")
            return CommandResult(
                returncode=self.grype_returncode,
                stdout="",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {request.command}")


def test_supply_chain_scan_passes_without_threshold_violation(tmp_path: Path) -> None:
    scanner = SyftGrypeScanner(
        FakeScanRunner(grype_returncode=0),
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    result = scanner.scan("sha256:" + "a" * 64, candidate_id="candidate-1")
    assert result.passed
    assert result.sbom_digest.startswith("sha256:")
    assert result.sbom_ref.startswith("file:")
    assert result.vulnerability_scan_ref.startswith("file:")


def test_supply_chain_scan_records_threshold_failure(tmp_path: Path) -> None:
    scanner = SyftGrypeScanner(
        FakeScanRunner(grype_returncode=2),
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
        fail_on="high",
    )
    result = scanner.scan("sha256:" + "a" * 64, candidate_id="candidate-1")
    assert not result.passed
