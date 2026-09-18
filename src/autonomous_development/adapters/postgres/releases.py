from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.models import ReleasedVersion
from autonomous_development.ports.persistence import (
    OperationConflictError,
    ReleasedVersionRepository,
    ServingReleaseReceipt,
)

from .schema import (
    released_versions,
    serving_release_operations,
    serving_releases,
)


class SqlReleasedVersionRepository(ReleasedVersionRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, release: ReleasedVersion) -> ReleasedVersion:
        existing = self.get(release.id)
        if existing is not None:
            if existing != release:
                raise ValueError(
                    f"release id already exists with different identity: {release.id}"
                )
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(released_versions).values(**_release_values(release)))
        except IntegrityError:
            existing = self.get(release.id)
            if existing is None or existing != release:
                raise
            return existing
        return release

    def get(self, release_id: str) -> ReleasedVersion | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(released_versions).where(released_versions.c.id == release_id)
                )
                .mappings()
                .first()
            )
        return _release_from_row(row) if row is not None else None

    def get_serving(self, target_id: str) -> ReleasedVersion | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(released_versions)
                    .join(
                        serving_releases,
                        serving_releases.c.release_id == released_versions.c.id,
                    )
                    .where(serving_releases.c.target_id == target_id)
                )
                .mappings()
                .first()
            )
        return _release_from_row(row) if row is not None else None

    def set_serving(
        self,
        target_id: str,
        release_id: str,
        *,
        operation_id: str,
    ) -> ServingReleaseReceipt:
        if not operation_id.strip():
            raise ValueError("operation_id must be non-empty")

        existing = self._get_operation(operation_id)
        if existing is not None:
            return _validate_receipt(existing, target_id, release_id)

        try:
            with self._engine.begin() as connection:
                release_row = (
                    connection.execute(
                        select(released_versions).where(released_versions.c.id == release_id)
                    )
                    .mappings()
                    .first()
                )
                if release_row is None:
                    raise ValueError(f"unknown release: {release_id}")
                if str(release_row["target_id"]) != target_id:
                    raise ValueError("release does not belong to target")

                current = (
                    connection.execute(
                        select(serving_releases.c.release_id).where(
                            serving_releases.c.target_id == target_id
                        )
                    )
                    .scalar_one_or_none()
                )
                receipt = ServingReleaseReceipt(
                    operation_id=operation_id,
                    target_id=target_id,
                    release_id=release_id,
                    previous_release_id=str(current) if current is not None else None,
                )
                connection.execute(
                    insert(serving_release_operations).values(
                        operation_id=receipt.operation_id,
                        target_id=receipt.target_id,
                        release_id=receipt.release_id,
                        previous_release_id=receipt.previous_release_id,
                    )
                )

                if current is None:
                    connection.execute(
                        insert(serving_releases).values(
                            target_id=target_id,
                            release_id=release_id,
                        )
                    )
                elif str(current) != release_id:
                    result = connection.execute(
                        update(serving_releases)
                        .where(
                            serving_releases.c.target_id == target_id,
                            serving_releases.c.release_id == str(current),
                        )
                        .values(release_id=release_id)
                    )
                    if result.rowcount != 1:
                        raise RuntimeError(
                            "serving release changed concurrently before pointer update"
                        )
        except IntegrityError as exc:
            persisted = self._get_operation(operation_id)
            if persisted is None:
                raise
            try:
                return _validate_receipt(persisted, target_id, release_id)
            except OperationConflictError as conflict:
                raise conflict from exc

        persisted = self._get_operation(operation_id)
        if persisted is None:
            raise RuntimeError("serving release operation receipt was not persisted")
        return _validate_receipt(persisted, target_id, release_id)

    def _get_operation(self, operation_id: str) -> ServingReleaseReceipt | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(serving_release_operations).where(
                        serving_release_operations.c.operation_id == operation_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        values = dict(row)
        previous = values.get("previous_release_id")
        return ServingReleaseReceipt(
            operation_id=str(values["operation_id"]),
            target_id=str(values["target_id"]),
            release_id=str(values["release_id"]),
            previous_release_id=str(previous) if previous is not None else None,
        )


def _validate_receipt(
    receipt: ServingReleaseReceipt,
    target_id: str,
    release_id: str,
) -> ServingReleaseReceipt:
    if receipt.target_id != target_id or receipt.release_id != release_id:
        raise OperationConflictError(
            f"operation id {receipt.operation_id} is already bound to another serving release"
        )
    return receipt


def _release_values(release: ReleasedVersion) -> dict[str, object]:
    return {
        "id": release.id,
        "target_id": release.target_id,
        "source_commit": release.source_commit,
        "source_tree": release.source_tree,
        "artifact_digest": release.artifact_digest,
        "objective_revision_id": release.objective_revision_id,
        "deployment_id": release.deployment_id,
        "promoted_at": release.promoted_at,
    }


def _release_from_row(row: RowMapping) -> ReleasedVersion:
    values = dict(row)
    promoted_at = values["promoted_at"]
    if not isinstance(promoted_at, datetime):
        raise RuntimeError("persisted release promoted_at is not a datetime")
    return ReleasedVersion(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        source_commit=str(values["source_commit"]),
        source_tree=str(values["source_tree"]),
        artifact_digest=str(values["artifact_digest"]),
        objective_revision_id=str(values["objective_revision_id"]),
        deployment_id=str(values["deployment_id"]),
        promoted_at=_utc(promoted_at),
    )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
