# Isolated V1 acceptance profile

The `target/` directory is a disposable, Dockerizable target for local acceptance. Keep its
database, DBOS system state, runtime state root and Docker resources separate from any existing
registered target. The target is intentionally not included in a production bootstrap manifest.

The current target definition and its immutable base-image/dependency choices are documented in
[target/README.md](target/README.md). Acceptance evidence is run-specific: health/readiness,
SBOM and vulnerability results must be collected from the image built for that run.
