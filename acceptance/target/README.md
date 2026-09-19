# Autonomous Development V1 acceptance target

This is a deliberately separate, tiny FastAPI target used only for local acceptance.
It is not the `autonomous-development` repository and must not be registered in the
production database. The acceptance runbook creates a separate PostgreSQL database,
DBOS system state, state root, Docker image and serving release for this target.

The image uses the pinned official Python 3.14.7 Alpine amd64 builder/runtime digest.
Dependencies are installed as root only in the disposable builder stage and copied into the
explicit non-root UID 65532 runtime image. This HTTP-only target uses ordinary `uvicorn` rather
than `uvicorn[standard]`. The final runtime replaces Alpine's zlib package with a locally built
package from upstream zlib commit `df84af25dc1942490e1d1c899a07619152a46148`, which contains
the upstream fix for CVE-2026-85091.
