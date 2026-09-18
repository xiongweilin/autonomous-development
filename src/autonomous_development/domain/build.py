from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DockerBuildSpec:
    dockerfile: str = "Dockerfile"
    context: str = "."
    dependency_lock_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.dockerfile.strip() or not self.context.strip():
            raise ValueError("Docker build paths must be non-empty")
        for value in (self.dockerfile, self.context, *self.dependency_lock_files):
            normalized = value.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError("Docker build paths must stay repository-relative")
