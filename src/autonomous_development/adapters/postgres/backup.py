from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from autonomous_development.ports.process import CommandRequest, ProcessRunner

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_OPERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


@dataclass(frozen=True, slots=True)
class PostgresBackupReceipt:
    operation_id: str
    source_database: str
    backup_path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PostgresRestoreReceipt:
    operation_id: str
    source_backup_sha256: str
    restored_database: str


class PostgresBackupError(RuntimeError):
    pass


class DockerPostgresBackup:
    def __init__(
        self,
        runner: ProcessRunner,
        *,
        container: str,
        database: str,
        user: str,
        backup_root: Path,
        timeout_seconds: int = 900,
    ) -> None:
        if not _CONTAINER.fullmatch(container):
            raise ValueError("PostgreSQL container identity is invalid")
        _safe_identifier(database, "database")
        _safe_identifier(user, "user")
        if not backup_root.is_absolute():
            raise ValueError("PostgreSQL backup root must be absolute")
        if timeout_seconds < 1:
            raise ValueError("PostgreSQL backup timeout must be positive")
        self._runner = runner
        self._container = container
        self._database = database
        self._user = user
        self._backup_root = backup_root
        self._timeout_seconds = timeout_seconds

    def backup(self, operation_id: str) -> PostgresBackupReceipt:
        safe_operation = _safe_operation(operation_id)
        self._backup_root.mkdir(parents=True, exist_ok=True)
        destination = (self._backup_root / f"{safe_operation}.dump").resolve()
        _require_child(self._backup_root, destination)
        if destination.exists():
            return self._receipt(operation_id, destination)

        remote = f"/tmp/autodev-{safe_operation}.dump"
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            self._run(
                "docker",
                "exec",
                self._container,
                "pg_dump",
                "--username",
                self._user,
                "--dbname",
                self._database,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                remote,
            )
            self._run(
                "docker",
                "cp",
                f"{self._container}:{remote}",
                str(temporary),
            )
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise PostgresBackupError("PostgreSQL backup artifact is empty")
            temporary.replace(destination)
        finally:
            self._cleanup_remote(remote)
            temporary.unlink(missing_ok=True)
        return self._receipt(operation_id, destination)

    def restore_isolated(
        self,
        receipt: PostgresBackupReceipt,
        *,
        restored_database: str,
        operation_id: str,
    ) -> PostgresRestoreReceipt:
        _safe_identifier(restored_database, "restored database")
        safe_operation = _safe_operation(operation_id)
        self._verify_receipt(receipt)
        remote = f"/tmp/autodev-restore-{safe_operation}.dump"
        try:
            self._run(
                "docker",
                "exec",
                self._container,
                "dropdb",
                "--username",
                self._user,
                "--if-exists",
                restored_database,
            )
            self._run(
                "docker",
                "exec",
                self._container,
                "createdb",
                "--username",
                self._user,
                restored_database,
            )
            self._run(
                "docker",
                "cp",
                str(receipt.backup_path),
                f"{self._container}:{remote}",
            )
            self._run(
                "docker",
                "exec",
                self._container,
                "pg_restore",
                "--username",
                self._user,
                "--dbname",
                restored_database,
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                remote,
            )
        except Exception:
            self.drop_database(restored_database)
            raise
        finally:
            self._cleanup_remote(remote)
        return PostgresRestoreReceipt(
            operation_id=operation_id,
            source_backup_sha256=receipt.sha256,
            restored_database=restored_database,
        )

    def drop_database(self, database: str) -> None:
        _safe_identifier(database, "database")
        self._run(
            "docker",
            "exec",
            self._container,
            "dropdb",
            "--username",
            self._user,
            "--if-exists",
            database,
        )

    def _verify_receipt(self, receipt: PostgresBackupReceipt) -> None:
        path = receipt.backup_path.resolve()
        _require_child(self._backup_root, path)
        if receipt.source_database != self._database:
            raise PostgresBackupError("backup receipt belongs to another source database")
        if not path.is_file():
            raise PostgresBackupError("backup artifact does not exist")
        digest = _sha256(path)
        if digest != receipt.sha256 or path.stat().st_size != receipt.size_bytes:
            raise PostgresBackupError("backup artifact no longer matches its receipt")

    def _receipt(self, operation_id: str, path: Path) -> PostgresBackupReceipt:
        return PostgresBackupReceipt(
            operation_id=operation_id,
            source_database=self._database,
            backup_path=path,
            sha256=_sha256(path),
            size_bytes=path.stat().st_size,
        )

    def _cleanup_remote(self, remote: str) -> None:
        result = self._runner.run(
            CommandRequest(
                command=("docker", "exec", self._container, "rm", "-f", remote),
                cwd=self._backup_root,
                timeout_seconds=min(self._timeout_seconds, 60),
            )
        )
        if result.returncode not in {0, 1}:
            raise PostgresBackupError(
                f"failed to clean temporary PostgreSQL backup: {result.stderr}"
            )

    def _run(self, *command: str) -> None:
        result = self._runner.run(
            CommandRequest(
                command=tuple(command),
                cwd=self._backup_root,
                timeout_seconds=self._timeout_seconds,
            )
        )
        if result.returncode != 0:
            raise PostgresBackupError(
                f"PostgreSQL backup command failed: {command[1]}: {result.stderr}"
            )


def _safe_identifier(value: str, field_name: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} is not a safe PostgreSQL identifier")
    return value


def _safe_operation(value: str) -> str:
    if not _OPERATION.fullmatch(value):
        raise ValueError("backup operation id is not filesystem-safe")
    return value


def _require_child(root: Path, path: Path) -> None:
    resolved_root = root.resolve()
    if resolved_root not in path.parents:
        raise ValueError("backup path escaped its configured root")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()
