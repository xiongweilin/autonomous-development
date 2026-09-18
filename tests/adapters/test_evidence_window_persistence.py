from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.evidence_windows import (
    SqlEvidenceWindowRepository,
)
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.domain.models import EvidenceWindow


def test_evidence_window_roundtrips_immutably() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlEvidenceWindowRepository(engine)
    now = datetime.now(UTC)
    window = EvidenceWindow(
        id="window-1",
        target_id="target-1",
        release_ids=("release-1",),
        opened_at=now - timedelta(minutes=30),
        closed_at=now,
        telemetry_refs=("telemetry:1",),
        feedback_refs=("feedback:1",),
        missing_evidence=("telemetry:latency",),
    )
    repository.add(window)
    assert repository.get("window-1") == window
    assert repository.add(window) == window
