from __future__ import annotations

from dataclasses import dataclass

from autonomous_development.ports.command import CommandSpec


REQUIRED_PREDEPLOY_GATES = frozenset({"static", "tests", "security"})


@dataclass(frozen=True, slots=True)
class VerificationGateSpec:
    id: str
    commands: tuple[CommandSpec, ...]
    required: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("gate id must be non-empty")
        if not self.commands:
            raise ValueError("verification gate requires at least one command")


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    gates: tuple[VerificationGateSpec, ...]

    def __post_init__(self) -> None:
        ids = tuple(gate.id for gate in self.gates)
        if len(set(ids)) != len(ids):
            raise ValueError("verification gate ids must be unique")

    @property
    def gate_ids(self) -> frozenset[str]:
        return frozenset(gate.id for gate in self.gates)

    def require_platform_baseline(self) -> None:
        missing = REQUIRED_PREDEPLOY_GATES - self.gate_ids
        if missing:
            raise ValueError(
                f"verification plan is missing required gates: {sorted(missing)}"
            )
