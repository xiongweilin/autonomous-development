from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CycleRecord(Base):
    __tablename__ = "development_cycles"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    objective_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_release_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[str | None] = mapped_column(String(128))
    verification_run_id: Mapped[str | None] = mapped_column(String(128))
    artifact_id: Mapped[str | None] = mapped_column(String(128))
    candidate_deployment_id: Mapped[str | None] = mapped_column(String(128))
    experiment_id: Mapped[str | None] = mapped_column(String(128))
    release_decision: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class CycleEventRecord(Base):
    __tablename__ = "cycle_events"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_cycle_events_operation_id"),
        UniqueConstraint(
            "cycle_id",
            "resulting_version",
            name="uq_cycle_events_cycle_resulting_version",
        ),
        Index("ix_cycle_events_cycle_version", "cycle_id", "resulting_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(String(192), nullable=False)
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("development_cycles.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_version: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(64), nullable=False)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
