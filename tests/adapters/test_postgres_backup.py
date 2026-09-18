from pathlib import Path

import pytest

from autonomous_development.adapters.postgres.backup import (
    DockerPostgresBackup,
    PostgresBackupError,
)
from autonomous_development.ports.process import CommandRequest, CommandResult


class FakeRunner:
    def __init__(self) -> None:
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        command = request.command
        if command[:2] == ("docker", "cp") and ":" in command[2]:
            Path(command[3]).write_bytes(b"postgres-backup")
        return CommandResult(returncode=0, stdout="", stderr="")


def service(tmp_path: Path, runner: FakeRunner) -> DockerPostgresBackup:
    return DockerPostgresBackup(
        runner,
        container="postgres-1",
        database="autodev",
        user="postgres",
        backup_root=(tmp_path / "backups").resolve(),
    )


def test_backup_is_content_addressed_and_replay_does_not_redump(tmp_path: Path) -> None:
    runner = FakeRunner()
    backup = service(tmp_path, runner)

    first = backup.backup("backup-1")
    request_count = len(runner.requests)
    replay = backup.backup("backup-1")

    assert replay == first
    assert first.sha256.startswith("sha256:")
    assert first.size_bytes > 0
    assert len(runner.requests) == request_count
    assert sum(
        1
        for request in runner.requests
        if "pg_dump" in request.command
    ) == 1


def test_restore_rejects_tampered_backup_before_database_effect(tmp_path: Path) -> None:
    runner = FakeRunner()
    backup = service(tmp_path, runner)
    receipt = backup.backup("backup-1")
    receipt.backup_path.write_bytes(b"tampered")
    count = len(runner.requests)

    with pytest.raises(PostgresBackupError, match="no longer matches"):
        backup.restore_isolated(
            receipt,
            restored_database="autodev_restore",
            operation_id="restore-1",
        )

    assert len(runner.requests) == count


def test_restore_database_identifier_is_strictly_bounded(tmp_path: Path) -> None:
    runner = FakeRunner()
    backup = service(tmp_path, runner)
    receipt = backup.backup("backup-1")

    with pytest.raises(ValueError, match="safe PostgreSQL identifier"):
        backup.restore_isolated(
            receipt,
            restored_database="restore;DROP DATABASE autodev",
            operation_id="restore-1",
        )
