from __future__ import annotations

from pathlib import Path
from typing import Protocol

from autonomous_development.domain.commands import CommandResult, CommandSpec


class CommandRunner(Protocol):
    def run(self, spec: CommandSpec, *, default_cwd: Path) -> CommandResult: ...
