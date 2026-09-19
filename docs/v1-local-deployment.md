# V1 Local Deployment and Bootstrap

Status: implementation-aligned local runbook
Last reviewed: 2026-09-19

This runbook is for the implemented V1 boundary: one registered target, one Windows workstation,
local Docker, local PostgreSQL/DBOS state, local Codex App Server, and a loopback control/product
proxy. It is not a remote-production deployment guide.

## 1. Preconditions

The control-plane checkout must pass the committed dependency and quality gates:

~~~powershell
uv sync --frozen --extra dev
uv lock --check
uv run ruff check .
uv run mypy src --no-incremental --show-error-codes
uv run lint-imports
uv run pytest -m "not integration"
~~~

Required live tools/services:

- Python 3.12-3.14;
- uv (CI is pinned to 0.12.17);
- git;
- Codex CLI/App Server;
- Docker daemon;
- k6;
- Syft;
- Grype;
- PostgreSQL for application state;
- a DBOS PostgreSQL system database;
- loopback Prometheus with at least one configured target telemetry query.

The target contract may name additional verification commands. Every command named by the
contract must be present on the host before readiness can pass.

## 2. Runtime environment

Set the application database, DBOS database, and state root. The state root must be an absolute
path.

~~~powershell
$env:AUTODEV_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/autodev"
$env:AUTODEV_DBOS_SYSTEM_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:5432/autodev_dbos"
$env:AUTODEV_STATE_ROOT = "C:\\autodev\\state"

$env:AUTODEV_PROMETHEUS_BASE_URL = "http://127.0.0.1:19090"
$env:AUTODEV_TELEMETRY_QUERIES = '{"requests":"sum(rate(http_requests_total{target_id=\"${target_id}\",release_id=\"${release_id}\"}[5m]))"}'
~~~

The PromQL above is only an example. Queries are target-owned and may use the
${target_id} and ${release_id} placeholders. An empty telemetry-query map is not
production-ready.

Default control-plane binding:

~~~text
API:            http://127.0.0.1:8765
Product proxy:  http://127.0.0.1:8765/product
Prometheus:     http://127.0.0.1:19090
~~~

V1 rejects non-loopback control/product routing.

## 3. Prepare the target repository

The target repository must be on its configured default branch, clean, and committed. V1 records
the exact baseline commit/tree and refuses to bootstrap from dirty or detached source.

The repository must contain autonomous-development.toml. The implemented schema is:

~~~toml
schema_version = 1
target_id = "example-product"

[build]
dockerfile = "Dockerfile"
dependency_locks = ["uv.lock"]

[verification]
[[verification.gates]]
id = "tests"
command = ["uv", "run", "pytest"]
timeout_seconds = 900

[deployment]
container_port = 8000
health_path = "/health"
readiness_path = "/ready"
startup_timeout_seconds = 60

[performance]
script_path = "tests/performance/smoke.js"
required_threshold_metrics = ["http_req_failed"]
timeout_seconds = 900

[canary]
max_candidate_error_rate = 0.02
max_error_rate_delta = 0.01
max_candidate_p95_latency_ms = 250.0
max_p95_latency_ratio = 1.25

[[canary.stages]]
weight_percent = 10
min_duration_seconds = 60
min_requests = 100

[[canary.stages]]
weight_percent = 50
min_duration_seconds = 120
min_requests = 500

[[canary.stages]]
weight_percent = 100
min_duration_seconds = 300
min_requests = 1000
~~~

The file's raw bytes are hashed with SHA-256 at registration. Runtime composition, readiness,
and new autonomous cycles fail closed if the contract changes without an explicit new
registration/revision. Autonomous candidates are not allowed to modify this file.

## 4. Prepare the baseline image

Bootstrap does not build or pull the initial product image. The baseline image must already exist
in the local Docker daemon. Bootstrap uses --pull never and starts the image by immutable Docker
image ID.

For example:

~~~powershell
docker build -t example-product:baseline C:\\src\\example-product
$baselineDigest = docker image inspect --format '{{.Id}}' example-product:baseline
$baselineDigest
~~~

Use the resulting sha256 image ID as baseline_release.artifact_digest.

The baseline image must expose the contract's container_port and return successful responses on
both health and readiness paths.

## 5. Create the bootstrap manifest

Bootstrap owns the initial human-approved objective, mutation bounds, exact baseline source
identity, and the first serving release. Example:

~~~json
{
  "target_id": "example-product",
  "repository": "C:\\src\\example-product",
  "default_branch": "main",
  "objective": {
    "id": "objective-1",
    "statement": "Improve correctness without reliability or performance regression.",
    "acceptance_criteria": [
      "Known user-visible correctness defects are fixed without regression."
    ],
    "primary_metrics": ["correctness"],
    "reliability_constraints": ["Error rate must not regress."],
    "performance_constraints": ["p95 latency must remain within the target contract."],
    "security_constraints": ["No secret exposure."],
    "mutation_policy": {
      "allowed_paths": ["src", "tests"],
      "forbidden_paths": ["deploy"],
      "max_changed_files": 5,
      "max_implementation_attempts": 2
    },
    "created_at": "2026-09-19T00:00:00Z"
  },
  "baseline_release": {
    "id": "release-1",
    "artifact_digest": "sha256:REPLACE_WITH_64_HEX_DIGITS",
    "deployment_id": "baseline-1",
    "promoted_at": "2026-09-19T00:00:00Z"
  }
}
~~~

Use real timezone-aware timestamps and a real 64-hex-character image digest.

## 6. Bootstrap

From the autonomous-development repository:

~~~powershell
uv run autonomous-development bootstrap --manifest C:\\autodev\\bootstrap.json
~~~

Bootstrap performs these steps synchronously:

1. upgrades the application database to Alembic head;
2. verifies the clean target default branch and records its commit/tree;
3. validates and hashes the target contract;
4. ensures the immutable baseline Docker deployment exists;
5. independently observes health/readiness;
6. registers the objective, target, release, and serving pointer.

The operation is replay-safe when the manifest and external identities are unchanged. It fails
closed on a conflicting target, contract identity, source state, release identity, or deployment
identity.

## 7. Start and verify the control plane

Start the runtime:

~~~powershell
uv run autonomous-development serve
~~~

In a second shell with the same environment:

~~~powershell
uv run autonomous-development ready
~~~

The readiness command must report all checks ready. The implemented checks cover the application
database, Python, git, uv, Codex, Docker, k6, Syft, Grype, exactly one registered target and
objective, a serving release, target contract identity/revision and gate commands, Prometheus,
and the mounted canary proxy.

The HTTP surfaces are also available at:

~~~text
GET http://127.0.0.1:8765/health
GET http://127.0.0.1:8765/ready
~~~

Do not treat /health alone as deployment acceptance; /ready is the fail-closed runtime boundary.

## 8. Route product traffic and submit attributable feedback

Product requests go through the mounted proxy:

~~~text
http://127.0.0.1:8765/product/<target-path>
~~~

The proxy returns server-owned attribution headers:

- x-autodev-arm;
- x-autodev-experiment;
- x-autodev-request-ref;
- x-autodev-session.

A stable session remains on a deterministic arm for one experiment. During canary, the request
reference is persisted before the request is forwarded. If attribution cannot be persisted, the
proxy fails closed.

Submit feedback using the returned request reference:

~~~powershell
$body = @{
  feedback_id = "feedback-1"
  kind = "explicit"
  category = "incorrect-result"
  severity = 4
  request_ref = "<x-autodev-request-ref>"
  free_text = "The result is incorrect for this request."
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/v1/targets/example-product/feedback" -ContentType "application/json" -Body $body
~~~

For control traffic, the server can return both release and deployment identity. For a
pre-promotion candidate request, release_id is intentionally null while deployment_id and
experiment_id identify the actual candidate. Once that deployment is promoted, evidence-window
queries can attribute the earlier candidate feedback by deployment identity.

Client-supplied reported release/deployment IDs are only corroborating claims. A mismatch with
server-owned attribution is rejected.

## 9. Autonomous cycle behavior

Eligible feedback is consumed by the durable feedback scheduler. A prepared cycle executes within
the human-owned objective and mutation policy:

~~~text
feedback/evidence
 -> Codex diagnosis
 -> bounded ChangeProposal
 -> isolated autodev/<cycle-id> worktree
 -> Codex implementation
 -> changed-path and changed-file-budget checks
 -> deterministic verification
 -> immutable Docker build + SBOM/vulnerability scan
 -> candidate deployment
 -> k6 performance gate
 -> staged canary
 -> promote or rollback
 -> post-promotion soak
 -> complete
~~~

Codex is restricted to the bounded workspace for repository reads/writes, has no deployment
authority, and runs implementation turns without network access. The orchestrator owns commits,
Docker, traffic, release decisions, and rollback.

## 10. Rollback and terminal cleanup

Before soak completes, the previous release remains a real rollback target.

On post-promotion regression, V1 restores all relevant pointers:

- traffic is restored to control;
- the serving release pointer is restored to the baseline release;
- the local default branch is restored to the exact baseline source commit;
- the candidate deployment is stopped;
- the autonomous worktree/branch is removed;
- the cycle becomes ROLLED_BACK.

If soak observation itself cannot produce trustworthy evidence, V1 performs the same fail-closed
rollback.

On successful soak completion, the previous baseline container and terminal worktree/branch are
reclaimed. Pre-promotion terminal failures/rejections also reclaim candidate resources on a
best-effort, replay-safe path.

## 11. Dependency and CI reproducibility

uv.lock is committed. Normal development and CI must not silently re-resolve dependencies:

~~~powershell
uv sync --frozen --extra dev
uv lock --check
~~~

CI additionally runs Ruff, strict mypy, Import Linter, unit/integration coverage, Alembic
PostgreSQL roundtrip, backup/restore tests, actionlint, zizmor, Gitleaks, and OSV scanning.

A dependency vulnerability or lock drift is a failing gate, not an advisory artifact.

## 12. V1 boundary

This runbook does not authorize:

- public-network exposure;
- arbitrary remote production deployment;
- autonomous objective changes;
- autonomous modification of autonomous-development.toml;
- host-global package installation by Codex;
- direct Codex access to deployment credentials;
- multi-target concurrent operation.

Those are outside V1.
