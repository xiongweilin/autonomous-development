from __future__ import annotations

from dbos import DBOS, DBOSConfig


def launch_dbos(
    system_database_url: str,
    *,
    application_version: str = "0.1.0",
) -> None:
    if not system_database_url.strip():
        raise ValueError("system_database_url must be non-empty")
    config: DBOSConfig = {
        "name": "autonomous-development",
        "application_version": application_version,
        "system_database_url": system_database_url,
    }
    DBOS(config=config)
    DBOS.launch()


def shutdown_dbos() -> None:
    DBOS.destroy(workflow_completion_timeout_sec=5)
