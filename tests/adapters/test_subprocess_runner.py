import sys
from pathlib import Path

import pytest

from autonomous_development.adapters.process import SubprocessRunner
from autonomous_development.ports.process import CommandRequest, CommandUnavailable


def test_subprocess_runner_captures_bounded_result(tmp_path: Path) -> None:
    runner = SubprocessRunner(max_output_chars=1024)
    result = runner.run(
        CommandRequest(
            command=(sys.executable, "-c", "print('ok')"),
            cwd=tmp_path.resolve(),
            timeout_seconds=10,
        )
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_subprocess_runner_rejects_missing_command(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    with pytest.raises(CommandUnavailable):
        runner.run(
            CommandRequest(
                command=("definitely-not-a-real-autodev-command",),
                cwd=tmp_path.resolve(),
                timeout_seconds=10,
            )
        )


def test_subprocess_runner_prefers_runtime_python_on_path(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    result = runner.run(
        CommandRequest(
            command=("python", "-c", "import pytest; print('pytest-ready')"),
            cwd=tmp_path.resolve(),
            timeout_seconds=10,
        )
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "pytest-ready"
