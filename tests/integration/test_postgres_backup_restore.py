from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from autonomous_development.adapters.postgres.backup import DockerPostgresBackup
from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.process.subprocess_runner import SubprocessRunner
from autonomous_development.application.cycles import CycleService
from autonomous_development.domain.models import DevelopmentCycle

pytestmark = pytest.mark.integration


def test_postgres_backup_restores_cycle_into_isolated_database() -> None:
    database_url = os.environ["AUTODEV_DATABASE_URL"]
    container = os.environ["AUTODEV_POSTGRES_CONTAINER"]
    suffix = uuid4().hex
    cycle_id = f"backup-cycle-{suffix}"
    source_engine = create_engine(database_url)
    source = CycleService(SqlCycleRepository(source_engine))
    source.create(
        DevelopmentCycle(
            id=cycle_id,
            target_id=f"target-{suffix}",
            objective_revision_id="objective-1",
            baseline_release_id="release-1",
        )
    )

    backup_root = (
        Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
        / f"autodev-backup-{suffix}"
    ).resolve()
    backup = DockerPostgresBackup(
        SubprocessRunner(),
        container=container,
        database="autodev",
        user="postgres",
        backup_root=backup_root,
    )
    restored_database = f"restore_{suffix[:20]}"
    receipt = backup.backup(f"backup-{suffix}")
    source_engine.dispose()

    restore = backup.restore_isolated(
        receipt,
        restored_database=restored_database,
        operation_id=f"restore-{suffix}",
    )
    assert restore.source_backup_sha256 == receipt.sha256

    restored_url = make_url(database_url).set(database=restored_database)
    restored_engine = create_engine(restored_url)
    try:
        recovered = CycleService(SqlCycleRepository(restored_engine)).get(cycle_id)
        assert recovered.id == cycle_id
        assert recovered.target_id == f"target-{suffix}"
    finally:
        restored_engine.dispose()
        backup.drop_database(restored_database)
        receipt.backup_path.unlink(missing_ok=True)
        backup_root.rmdir()
