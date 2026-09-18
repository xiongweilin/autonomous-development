from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandRequest:
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: int = 900
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command or not self.command[0].strip():
            raise ValueError("command must be non-empty")
        if not self.cwd.is_absolute():
            raise ValueError("command cwd must be absolute")
        if self.timeout_seconds < 1:
            raise ValueError("command timeout must be positive")


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    def run(self, request: CommandRequest) -> CommandResult: ...


class CommandUnavailable(RuntimeError):
    pass


class CommandTimedOut(RuntimeError):
    pass
