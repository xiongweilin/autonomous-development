from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: tuple[str, ...]
    timeout_seconds: int = 300
    cwd: Path | None = None
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0].strip():
            raise ValueError("command argv must be non-empty")
        if self.timeout_seconds < 1:
            raise ValueError("command timeout must be positive")
        if self.cwd is not None and not self.cwd.is_absolute():
            raise ValueError("command cwd must be absolute")


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class CommandRunner(Protocol):
    def run(self, spec: CommandSpec, *, default_cwd: Path) -> CommandResult: ...
