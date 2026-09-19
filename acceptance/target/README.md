# Autonomous Development V1 acceptance target

This is a deliberately separate, tiny FastAPI target used only for local acceptance.
It is not the `autonomous-development` repository and must not be registered in the
production database. The acceptance runbook creates a separate PostgreSQL database,
DBOS system state, state root, Docker image and serving release for this target.

The image uses pinned Chainguard Python builder and runtime digests. Dependencies are installed
in the matching builder image and copied into the non-root runtime image so the final artifact
can pass the configured Grype `high` threshold without weakening the security gate.
