from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BuildRequest:
    candidate_id: str
    context_dir: Path
    dockerfile: Path

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        if not self.context_dir.is_absolute() or not self.dockerfile.is_absolute():
            raise ValueError("build paths must be absolute")
        if not self.dockerfile.is_relative_to(self.context_dir):
            raise ValueError("Dockerfile must be inside the candidate build context")


@dataclass(frozen=True, slots=True)
class BuiltImage:
    image_digest: str
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class SupplyChainEvidence:
    sbom_digest: str
    sbom_ref: str
    vulnerability_scan_ref: str
    passed: bool


class BuildProvider(Protocol):
    def build(self, request: BuildRequest) -> BuiltImage: ...


class SupplyChainScanner(Protocol):
    def scan(self, image_digest: str, *, candidate_id: str) -> SupplyChainEvidence: ...


class BuildProviderError(RuntimeError):
    pass


class SupplyChainScanError(RuntimeError):
    pass
