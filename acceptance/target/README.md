# Autonomous Development V1 acceptance target

This is a deliberately separate, tiny FastAPI target used only for local acceptance.
It is not the `autonomous-development` repository and must not be registered in the
production database. The acceptance runbook creates a separate PostgreSQL database,
DBOS system state, state root, Docker image and serving release for this target.
