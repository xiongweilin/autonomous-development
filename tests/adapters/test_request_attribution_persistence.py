from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.request_attributions import (
    SqlRequestAttributionRepository,
)
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.domain.models import RequestAttribution


def repository() -> SqlRequestAttributionRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return SqlRequestAttributionRepository(engine)


def attribution(
    *,
    request_ref: str = "request-1",
    arm: str = "candidate",
    release_id: str | None = None,
    deployment_id: str | None = "deployment-2",
) -> RequestAttribution:
    return RequestAttribution(
        request_ref=request_ref,
        target_id="target-1",
        observed_at=datetime.now(UTC),
        arm=arm,
        experiment_id="experiment-1",
        release_id=release_id,
        deployment_id=deployment_id,
    )


def test_request_attribution_roundtrips_and_replays() -> None:
    repo = repository()
    value = attribution()

    assert repo.add(value) == value
    assert repo.get(value.request_ref) == value
    assert repo.add(value) == value


def test_request_reference_cannot_be_rebound() -> None:
    repo = repository()
    repo.add(attribution())

    with pytest.raises(ValueError, match="different attribution"):
        repo.add(
            attribution(
                arm="control",
                release_id="release-1",
                deployment_id=None,
            )
        )


def test_unknown_request_reference_returns_none() -> None:
    assert repository().get("missing-request") is None
