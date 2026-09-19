from __future__ import annotations

from pathlib import PurePosixPath

from .models import ChangeProposal


class ScopeViolation(ValueError):
    """A candidate changed paths outside its authorized mutation scope."""


def validate_changed_paths(proposal: ChangeProposal, changed_paths: tuple[str, ...]) -> None:
    if not changed_paths:
        raise ScopeViolation("candidate must contain at least one changed path")
    if len(changed_paths) > proposal.max_changed_files:
        raise ScopeViolation(
            f"candidate changes {len(changed_paths)} files, exceeding budget "
            f"{proposal.max_changed_files}"
        )
    if "autonomous-development.toml" in {
        item.replace("\\", "/").strip("/") for item in changed_paths
    }:
        raise ScopeViolation("target contract is system-owned and cannot be changed autonomously")
    allowed = tuple(_normalize_prefix(path) for path in proposal.allowed_paths)
    forbidden = tuple(_normalize_prefix(path) for path in proposal.forbidden_paths)
    for raw_path in changed_paths:
        path = _normalize_path(raw_path)
        if any(_matches(path, prefix) for prefix in forbidden):
            raise ScopeViolation(f"changed path is explicitly forbidden: {raw_path}")
        if not any(_matches(path, prefix) for prefix in allowed):
            raise ScopeViolation(f"changed path is outside allowed scope: {raw_path}")


def _normalize_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise ScopeViolation(f"invalid repository-relative path: {value}")
    return PurePosixPath(normalized)


def _normalize_prefix(value: str) -> PurePosixPath:
    return _normalize_path(value.rstrip("/*"))


def _matches(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path == prefix or prefix in path.parents
