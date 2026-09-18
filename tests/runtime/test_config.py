from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from autonomous_development.runtime.config import RuntimeSettings


def settings(tmp_path: Path, **overrides: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "database_url": SecretStr("sqlite+pysqlite:///:memory:"),
        "dbos_system_database_url": SecretStr("sqlite:///:memory:"),
        "state_root": tmp_path.resolve(),
        "telemetry_queries": {"requests": "sum(rate(http_requests_total[5m]))"},
    }
    values.update(overrides)
    return RuntimeSettings(**values)


def test_runtime_settings_derive_state_paths(tmp_path: Path) -> None:
    configured = settings(tmp_path)

    assert configured.evidence_root == tmp_path.resolve() / "evidence"
    assert configured.traffic_state_root == tmp_path.resolve() / "traffic"
    assert configured.worktree_root == tmp_path.resolve() / "worktrees"
    assert configured.codex_thread_journal_root == tmp_path.resolve() / "codex-threads"


def test_runtime_settings_reject_non_loopback_services(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, canary_proxy_base_url="http://example.com:8766")

    with pytest.raises(ValidationError):
        settings(tmp_path, prometheus_base_url="http://example.com:19090")


def test_runtime_settings_reject_relative_state_root() -> None:
    with pytest.raises(ValidationError):
        RuntimeSettings(
            database_url=SecretStr("sqlite+pysqlite:///:memory:"),
            dbos_system_database_url=SecretStr("sqlite:///:memory:"),
            state_root=Path("relative"),
        )
