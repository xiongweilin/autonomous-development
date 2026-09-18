from __future__ import annotations

from autonomous_development.domain.models import ReleasedVersion
from autonomous_development.ports.persistence import ReleasedVersionRepository


class ReleaseNotFoundError(LookupError):
    pass


class ReleaseCatalogService:
    def __init__(self, repository: ReleasedVersionRepository) -> None:
        self._repository = repository

    def register(self, release: ReleasedVersion) -> ReleasedVersion:
        return self._repository.add(release)

    def get(self, release_id: str) -> ReleasedVersion:
        release = self._repository.get(release_id)
        if release is None:
            raise ReleaseNotFoundError(release_id)
        return release

    def serving(self, target_id: str) -> ReleasedVersion | None:
        return self._repository.get_serving(target_id)

    def set_serving(
        self,
        target_id: str,
        release_id: str,
        *,
        operation_id: str,
    ) -> ReleasedVersion:
        if not operation_id.strip():
            raise ValueError("operation_id must be non-empty")
        release = self.get(release_id)
        if release.target_id != target_id:
            raise ValueError("release does not belong to target")
        self._repository.set_serving(
            target_id,
            release_id,
            operation_id=operation_id,
        )
        serving = self._repository.get_serving(target_id)
        if serving is None or serving.id != release_id:
            raise RuntimeError("serving release pointer did not reconcile")
        return serving
