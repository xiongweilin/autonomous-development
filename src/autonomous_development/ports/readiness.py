from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    ready: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: tuple[ReadinessCheck, ...]


class ReadinessProvider(Protocol):
    def check(self) -> ReadinessReport: ...
