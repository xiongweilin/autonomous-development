from __future__ import annotations

import os
import subprocess

from autonomous_development.ports.process import (
    CommandRequest,
    CommandResult,
    CommandTimedOut,
    CommandUnavailable,
)

_DEFAULT_ENVIRONMENT_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
)


class SubprocessRunner:
    def __init__(self, *, max_output_chars: int = 131_072) -> None:
        if max_output_chars < 1024:
            raise ValueError("max_output_chars must be at least 1024")
        self._max_output_chars = max_output_chars

    def run(self, request: CommandRequest) -> CommandResult:
        environment = {
            key: value
            for key in _DEFAULT_ENVIRONMENT_KEYS
            if (value := os.environ.get(key)) is not None
        }
        environment.update(request.environment)
        try:
            completed = subprocess.run(
                list(request.command),
                cwd=request.cwd,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=request.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CommandUnavailable(request.command[0]) from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandTimedOut(
                f"command exceeded {request.timeout_seconds}s: {request.command[0]}"
            ) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=_bounded(completed.stdout, self._max_output_chars),
            stderr=_bounded(completed.stderr, self._max_output_chars),
        )


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = "\n...[truncated by autonomous-development]...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return value[:head] + marker + value[-tail:]
