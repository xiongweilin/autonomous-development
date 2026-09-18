from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from autonomous_development.ports.command import CommandResult, CommandRunner, CommandSpec


class LocalCommandRunner(CommandRunner):
    """Execute an argv-only command without a shell."""

    def run(self, spec: CommandSpec, *, default_cwd: Path) -> CommandResult:
        cwd = (spec.cwd or default_cwd).resolve()
        base = default_cwd.resolve()
        if cwd != base and base not in cwd.parents:
            raise ValueError(f"command cwd escaped workspace: {cwd}")

        environment = os.environ.copy()
        if spec.env is not None:
            environment.update(spec.env)

        started = time.monotonic()
        try:
            result = subprocess.run(
                list(spec.argv),
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            return CommandResult(
                argv=spec.argv,
                exit_code=None,
                stdout=_coerce_output(exc.stdout),
                stderr=_coerce_output(exc.stderr),
                duration_seconds=duration,
                timed_out=True,
            )
        return CommandResult(
            argv=spec.argv,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=time.monotonic() - started,
        )


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
