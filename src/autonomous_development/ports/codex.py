from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class CodexSandbox(StrEnum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"


@dataclass(frozen=True, slots=True)
class CodexTurnRequest:
    prompt: str
    cwd: Path
    sandbox: CodexSandbox
    thread_id: str | None = None
    resume_key: str | None = None
    model: str | None = None
    output_schema: Mapping[str, object] | None = None
    timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("Codex prompt must be non-empty")
        if not self.cwd.is_absolute():
            raise ValueError("Codex cwd must be absolute")
        if self.timeout_seconds < 1:
            raise ValueError("Codex timeout must be positive")
        if self.resume_key is not None and not self.resume_key.strip():
            raise ValueError("Codex resume key must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class CodexEvent:
    method: str
    params: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str
    status: str
    events: tuple[CodexEvent, ...]
    agent_messages: tuple[str, ...]

    @property
    def completed(self) -> bool:
        return self.status == "completed"


class CodexProvider(Protocol):
    def run_turn(self, request: CodexTurnRequest) -> CodexTurnResult: ...


class CodexProviderError(RuntimeError):
    """Codex could not complete the requested bounded engineering turn."""
