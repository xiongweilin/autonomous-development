# V1 Design — Autonomous Development Closed Loop

Status: implementation acceptance candidate  
Scope: V1 only  
Owner: this document  
Last reviewed: 2026-09-19

This document is the canonical V1 architecture and lifecycle definition. README is navigation only. Runtime/tool facts belong in v1-runtime-dependencies.md. Research history belongs in v1-research-basis.md.

## 1. Goal

V1 must prove one thing end to end:

> A bounded software/Agent product can receive a human-owned requirement, be implemented by Codex, deployed, used, observed, improved from attributable user feedback and runtime data, tested again, progressively exposed to traffic, promoted or rolled back, and continue that loop without a human deciding each concrete code change.

The product-development loop is:

~~~text
Requirement / current product objective
        |
        v
Current released version
        |
        v
User traffic + telemetry + explicit feedback
        |
        v
Evidence window
        |
        v
Diagnosis
        |
        v
Change proposal
        |
        v
Isolated worktree
        |
        v
Codex implementation
        |
        v
Deterministic verification + Codex review
        |
        v
Immutable build artifact
        |
        v
Sandbox / candidate deployment
        |
        v
Offline eval + load/performance gates
        |
        v
Canary 10% -> 50% -> 100%
        |
        +---- hard regression ----> rollback / reject
        |
        v
Promotion
        |
        v
Post-promotion soak
        |
        v
New released version
        |
        +--------------------------> next evidence window
~~~

V1 is local-first and is intended to be deployed to the owner's Windows workstation only after implementation is complete.

## 2. V1 non-goals

V1 deliberately does not:

1. implement its own coding agent, code editor agent, shell agent or repository-understanding model;
2. directly call foundation-model APIs for software engineering; Codex is the engineering executor;
3. import agent-kernel, meta-controller or administrative-orchestrator;
4. autonomously change the product's top-level objective or business constraints;
5. autonomously modify this autonomous-development repository itself;
6. grant Codex production credentials or release authority;
7. use an LLM judgment as the sole basis for promotion;
8. install host-global software autonomously;
9. use Kubernetes or require a cluster;
10. support arbitrary remote production environments in V1;
11. promise defect-free software. V1 guarantees enforceable process invariants and fail-closed gates, not perfect program correctness.

Future versions may add adapters for external runtimes, policy systems, Kubernetes progressive delivery, remote hosts, or controlled self-development without changing V1 domain semantics.

## 3. Design principles

### 3.1 Codex executes engineering; this system controls the lifecycle

Codex owns implementation work inside an isolated workspace:

- inspect repository;
- reproduce failures;
- edit files;
- add or modify tests;
- run permitted local checks;
- respond to review findings.

Autonomous Development owns:

- why a development cycle exists;
- which evidence belongs to it;
- what scope may change;
- which workspace and source revision are authoritative;
- which checks are mandatory;
- which artifact was built;
- which deployment received traffic;
- which feedback belongs to which version;
- whether evidence is sufficient;
- whether a candidate may be promoted;
- rollback and recovery.

Codex output is always a proposal or implementation candidate, never release authority.

### 3.2 Evidence is not authority

The following distinctions are V1 invariants:

~~~text
UserFeedback              != ProductObjective
TelemetryObservation       != Diagnosis
Diagnosis                  != ChangeAuthorization
CodexOutput                != VerifiedChange
TestsPassed                != ReleaseCandidate
BuildSucceeded             != DeployableArtifact
DeploymentHealthy          != ProductImproved
MetricImprovement          != PromotionAuthority
CanaryCompleted            != ReleaseFinalized
GitCommit                  != ReleasedVersion
ProviderSuccess            != VerifiedExternalState
RetryableFailure           != RetryPermission
~~~

Every promotion must be reconstructed from durable evidence, not inferred from a local command exit code or an agent statement.

### 3.3 Small autonomous changes

V1 optimizes for repeated small changes rather than rare large rewrites.

Every target defines a mutation budget:

- allowed and forbidden paths;
- maximum change scope;
- dependency policy;
- maximum number of implementation attempts per cycle;
- maximum wall-clock and Codex budget;
- explicit stop/escalation conditions.

A candidate that exceeds the budget becomes BLOCKED. V1 does not silently widen scope.

### 3.4 Mechanical rules before subjective judgment

If a property can be checked deterministically, it must not be delegated to an LLM.

Examples:

- schema validity;
- import/layer boundaries;
- lint/type checks;
- unit/integration tests;
- coverage;
- known-vulnerability policy;
- secret scanning;
- image scan;
- health/readiness;
- performance thresholds;
- canary error and latency regression;
- artifact identity;
- feedback-to-version binding.

Codex review is additive evidence for maintainability and defects that deterministic tools do not catch.

## 4. Bounded context and package architecture

Proposed Python package:

~~~text
src/autonomous_development/
    domain/
        models.py
        enums.py
        events.py
        policies.py
        transitions.py
    ports/
        codex.py
        repository.py
        ci.py
        build.py
        deployment.py
        traffic.py
        telemetry.py
        feedback.py
        secrets.py
        clock.py
    application/
        register_target.py
        start_cycle.py
        collect_evidence.py
        diagnose.py
        develop.py
        verify.py
        build_candidate.py
        deploy_candidate.py
        run_canary.py
        evaluate.py
        promote.py
        rollback.py
        recover.py
    adapters/
        codex_app_server/
        git_cli/
        github_cli/
        docker_cli/
        traefik/
        prometheus/
        local_feedback/
        credential_broker/
        postgres/
    workflows/
        development_cycle.py
        evidence_window.py
        canary.py
        post_promotion_soak.py
    api/
        app.py
        routes/
    cli/
        main.py
    runtime/
        composition.py
        config.py
        telemetry.py
~~~

Dependency direction:

~~~text
entrypoints/runtime
      |
      v
application -----> ports
      |             |
      v             v
    domain <----- adapters implement ports
~~~

Rules:

- domain imports no project infrastructure;
- ports may depend on domain types only;
- application depends on domain and ports;
- adapters implement ports and may depend on domain, but never own lifecycle semantics;
- adapters do not import each other;
- runtime/entrypoints are the only composition root.

Import Linter must enforce these boundaries in CI.

## 5. Core domain model

### 5.1 DevelopmentTarget

A registered product that may be autonomously developed.

Minimum identity:

- target_id;
- repository locator;
- default branch;
- target contract revision;
- active objective revision;
- current released version;
- mutation policy;
- verification policy;
- deployment profile;
- feedback/telemetry bindings.

Only one DevelopmentCycle may mutate one target at a time in V1. Observation may continue concurrently.

### 5.2 ProductObjectiveRevision

Human-owned, immutable once activated.

Contains:

- objective statement;
- acceptance criteria;
- primary product metrics;
- reliability constraints;
- performance constraints;
- safety/security constraints;
- cost/resource constraints where relevant;
- allowed autonomous mutation scope;
- explicit stop/escalation conditions.

Feedback may cause the system to propose work inside the objective. It cannot rewrite this object.

### 5.3 ReleasedVersion

A durable identity binding:

- source repository;
- source tree/commit identity;
- immutable artifact digest;
- target-contract revision;
- deployment identity;
- objective revision;
- promotion evidence set;
- release timestamp.

### 5.4 EvidenceWindow

A bounded set of observations used for one diagnosis.

It records:

- time interval;
- released/candidate versions included;
- request/session counts;
- telemetry query references and returned aggregates;
- feedback references;
- regression/eval failures;
- incidents/errors;
- evidence freshness;
- missing evidence.

An EvidenceWindow is immutable after closing.

### 5.5 Diagnosis

Structured Codex-produced analysis grounded in one EvidenceWindow.

Required fields:

- observed problem;
- affected user journey;
- evidence references;
- confidence;
- competing hypotheses;
- likely root cause;
- proposed change class;
- expected measurable outcome;
- risks;
- requested files/components;
- required validation.

Diagnosis is schema-validated and cannot itself start deployment.

### 5.6 ChangeProposal

A bounded implementation contract derived from a Diagnosis or initial Requirement.

Contains:

- exact baseline ReleasedVersion;
- objective revision;
- acceptance criteria;
- mutation scope;
- forbidden scope;
- implementation budget;
- mandatory test/eval set;
- rollback expectations.

### 5.7 CandidateRevision

Binds the actual change to reality:

- cycle ID;
- worktree path;
- branch name;
- base commit;
- candidate commit;
- git tree hash;
- diff summary;
- changed paths;
- dependency changes;
- Codex thread ID;
- implementation attempt number.

### 5.8 VerificationRun

One immutable execution of a gate suite.

Each check records:

- check ID and version;
- command/provider;
- start/end;
- exit result;
- machine-readable measurements;
- evidence/artifact references;
- logs locator;
- pass/fail/blocked.

A rerun creates another VerificationRun; it never overwrites history.

### 5.9 BuildArtifact

For V1, the deployable unit is an OCI/Docker image.

Identity:

- image ID/digest;
- candidate source tree hash;
- build definition digest;
- dependency lock digest;
- SBOM locator/digest;
- vulnerability scan result;
- build logs;
- reproducibility metadata.

Tags are pointers. Digest is identity.

### 5.10 Deployment

Binds one BuildArtifact to one environment and runtime identity.

States:

~~~text
planned -> starting -> ready -> serving -> draining -> stopped
                         |
                         +-> failed
~~~

Provider success alone does not establish ready/serving; readiness is independently observed.

### 5.11 Experiment / CanaryStage

An Experiment binds:

- control ReleasedVersion;
- candidate Deployment;
- traffic assignment policy;
- stage sequence;
- metric definitions;
- evidence sufficiency thresholds;
- rollback rules.

Default V1 stages:

~~~text
10% candidate -> 50% candidate -> 100% candidate
~~~

The percentages are defaults, not domain constants. Each target may override them.

A stage requires both:

- minimum exposure duration;
- minimum representative request/sample count.

If evidence is insufficient, the stage remains active. Insufficient evidence is not success.

### 5.12 UserFeedback

Feedback contains:

- feedback_id;
- target_id;
- received_at;
- request/session reference if available;
- explicit rating/verdict;
- category/tags;
- optional free text;
- severity;
- provenance.

At ingestion, the system attempts to bind it to:

- experiment;
- deployment/version;
- request/session;
- objective.

Unbound feedback may be stored and inspected but cannot count as causal evidence for automatic promotion.

### 5.13 ReleaseDecision

Machine-generated deterministic decision object:

- PROMOTE;
- REJECT;
- ROLLBACK;
- HOLD_INSUFFICIENT_EVIDENCE;
- BLOCKED.

It records every gate and evidence reference that supports the decision. There is no generic "LLM says good" condition.

## 6. Durable lifecycle

DevelopmentCycle states:

~~~text
NEW
 -> BASELINE_VERIFIED
 -> EVIDENCE_READY
 -> DIAGNOSED
 -> CHANGE_PROPOSED
 -> DEVELOPING
 -> CANDIDATE_READY
 -> VERIFYING
 -> VERIFIED
 -> BUILT
 -> STAGED
 -> CANARYING
 -> PROMOTION_READY
 -> PROMOTED
 -> SOAKING
 -> COMPLETED
~~~

Terminal/exception states:

~~~text
REJECTED
ROLLED_BACK
BLOCKED
CANCELLED
FAILED_RECOVERABLE
FAILED_TERMINAL
~~~

Transitions are explicit and version-bound. Stale commands fail closed.

The lifecycle is executed as DBOS durable workflows. Long waits, canary stages and soak periods use durable sleep/state rather than an in-memory scheduler.

## 7. Side-effect and retry rules

Every effectful provider operation has:

1. a durable operation ID/idempotency key;
2. preconditions;
3. execution;
4. receipt;
5. independent observation/reconciliation;
6. bounded retry policy;
7. rollback or terminal disposition.

Operations requiring reconciliation before retry include:

- git push when acknowledgement is ambiguous;
- PR creation/update;
- merge;
- image build result registration;
- container start/recreate;
- traffic-weight change;
- release tagging;
- rollback.

Rule:

~~~text
timeout / lost acknowledgement
    -> observe current reality
    -> reconcile operation identity
    -> only then decide retry
~~~

No blind repeated deployment, merge or traffic switch is allowed.

## 8. Codex integration

### 8.1 Protocol

V1 uses the local Codex App Server JSON-RPC surface as the primary adapter.

Reasons:

- reuses the installed Codex harness;
- supports thread start/resume/fork;
- streams tool and file-change events;
- supports review mode;
- exposes sandbox/approval controls;
- avoids implementing a second agent loop.

codex exec is retained only as a compatibility/smoke path, not the main runtime contract.

### 8.2 Codex execution profiles

Diagnosis profile:

- read-only sandbox;
- no file modification;
- structured JSON-schema output;
- receives only the closed EvidenceWindow and repository context.

Implementation profile:

- cwd is the candidate git worktree;
- workspace-write sandbox;
- network disabled by default;
- no production secrets;
- no deployment credentials;
- no host-global installation;
- no permission to modify outside the worktree;
- output is not trusted as verification.

Review profile:

- independent review thread or Codex review mode;
- reads base/candidate diff and verification results;
- cannot edit or promote;
- findings are structured by severity/category.

The orchestrator, not Codex, performs branch creation, final commits/push, CI observation, artifact build registration, deployment and traffic promotion. This keeps engineering capability separate from release authority.

## 9. Repository/workspace model

Each change runs in a dedicated git worktree:

~~~text
target checkout/
    <default branch>
state_root/
    worktrees/<cycle-id>/
~~~

Implemented V1 workflow:

1. verify the local default-branch checkout is clean and record its exact commit/tree;
2. create branch autodev/<cycle-id> and an isolated worktree from that baseline;
3. run Codex only inside the worktree;
4. mechanically enforce allowed/forbidden paths and the maximum changed-file count;
5. create exactly one candidate commit with durable Codex-thread provenance;
6. run deterministic verification, build, deployment, performance and canary gates from the recorded candidate identity;
7. only after a deterministic promotion decision, fast-forward the local default branch to the exact candidate commit;
8. keep the previous serving release and source baseline rollback-addressable through post-promotion soak;
9. on soak rollback, restore traffic, serving release and the local default branch to the exact baseline, stop the candidate deployment and clean the worktree/branch;
10. on terminal success or rejection, reclaim terminal worktree/branch and no-longer-needed containers.

The V1 autonomous runtime does not require a remote push, pull request or GitHub merge to execute
a local development cycle. GitHub Actions remain the repository's own CI/security surface.

No development cycle edits the primary checkout directly.

## 10. Target contract

A target repository opts in through autonomous-development.toml. The file describes integration
facts, not the human-owned product objective or mutation authorization.

Implemented V1 contract shape:

~~~toml
schema_version = 1
target_id = "example-agent"

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
~~~

Registration hashes the raw contract bytes with SHA-256 and stores that revision in the target
registry. Runtime composition, readiness and autonomous cycle preparation fail closed when the
loaded contract revision differs from the registered revision.

Mutation policy is separately human-owned in ProductObjectiveRevision/DevelopmentTarget.
autonomous-development.toml is system-owned and cannot be modified by an autonomous candidate.

V1 executes target verification commands only after explicit target bootstrap/registration and
within the registered contract and mutation boundaries.

## 11. Quality gates

Quality is a sequence of independent gates. A later gate never turns an earlier failure into success.

### G0 — Baseline and scope

Must prove:

- baseline branch/commit exists;
- working source is clean;
- target contract is current;
- objective revision is current;
- no concurrent mutating cycle exists;
- baseline deployment is healthy;
- rollback target is available.

### G1 — Patch integrity

Must prove:

- changed paths are within scope;
- the actual changed-file count is within the human-owned max_changed_files budget;
- autonomous-development.toml and other forbidden files are untouched;
- generated/vendor/binary changes obey policy;
- dependency changes obey dependency policy;
- candidate commit/tree are recorded;
- no unrelated dirty state.

### G2 — Static and architectural quality

Required for this repository and recommended target defaults:

- Ruff;
- mypy strict for first-party code;
- Import Linter architecture contracts;
- formatting/check mode;
- actionlint for GitHub workflows;
- zizmor for workflow security.

Any target may add its own deterministic checks.

### G3 — Tests and behavioral regression

Required:

- unit tests;
- integration tests;
- contract tests for provider boundaries;
- negative/counterexample tests for state transitions;
- restart/recovery tests for durable workflows;
- property-based tests with Hypothesis on core transitions and idempotency;
- coverage threshold;
- known regression corpus.

Coverage is a gate but never treated as proof of correctness.

### G4 — Security and supply chain

Required:

- Gitleaks;
- OSV-Scanner source/dependency scan;
- SBOM generation with Syft for the candidate image;
- image vulnerability scan with Grype;
- committed dependency lockfile consistency with frozen installation;
- pinned GitHub Actions by full commit SHA.

GitHub dependency review is enabled where supported and used as an additional PR gate.

### G5 — Code health

Required:

- Sonar quality gate when the target is configured for Sonar;
- no new blocker/critical reliability or security issue;
- new-code coverage and duplication satisfy target policy;
- no unresolved high-severity Codex review finding;
- architectural "golden principles" expressed as machine-checkable rules wherever possible.

A target without Sonar may still pass V1 if equivalent local gates are configured. The autonomous-development repository itself should use SonarCloud.

### G6 — Build and startup

Must prove:

- image builds from recorded source;
- artifact digest is recorded;
- SBOM and scan correspond to the same artifact;
- service starts from clean state;
- health/readiness succeed;
- migration/startup checks succeed where applicable;
- candidate does not mutate control/baseline state unexpectedly.

### G7 — Offline product eval

A target supplies task-oriented eval cases separate from unit tests.

Each case records:

- input/fixture;
- success oracle;
- safety/forbidden outcomes;
- baseline score;
- candidate score;
- cost/latency if relevant.

Promotion requires no hard regression and target-specific minimum improvement or non-regression conditions.

LLM graders may be added as advisory evaluators, but V1 promotion requires at least one deterministic/product-grounded oracle for each critical acceptance criterion.

### G8 — Performance

k6 is the V1 load/performance runner.

Each target defines absolute thresholds and baseline-relative thresholds. At minimum:

- request/error rate;
- p95 latency;
- timeout rate;
- throughput under a defined load profile.

Performance must be tested before live canary. A candidate cannot compensate for a reliability regression with a product-quality gain unless the human-owned objective explicitly allows that tradeoff.

### G9 — Canary

For every stage, compare candidate and control over the same period where possible.

Hard rollback examples:

- readiness failure;
- crash/restart loop;
- material 5xx/timeout regression;
- p95 latency outside configured bound;
- explicit blocking user regression;
- product success metric below hard floor;
- security/safety signal.

Soft/product metrics can hold progression but cannot override hard reliability gates.

### G10 — Post-promotion soak

The previous release remains rollback-addressable until soak completes.

During soak:

- 100% traffic serves candidate;
- the same hard metrics remain active;
- fresh feedback is attributed;
- rollback remains automatic on hard regression.

Only then does the cycle become COMPLETED.

## 12. Performance and canary analysis model

V1 intentionally avoids pseudo-precise statistical scoring.

The default analyzer uses:

- hard absolute SLO thresholds;
- control-vs-candidate relative regression thresholds;
- minimum duration;
- minimum sample/request count;
- explicit evidence freshness.

This follows the principle that a canary must be representative and attributable. If traffic is too small, V1 returns HOLD_INSUFFICIENT_EVIDENCE rather than inventing confidence.

A future ExperimentAnalyzer port may add sequential statistics or Bayesian analysis without changing Experiment or ReleaseDecision semantics.

## 13. Feedback loop

Feedback enters through the local endpoint:

~~~text
POST /v1/targets/{target_id}/feedback
~~~

Product requests are served through the loopback /product proxy. For each routed request the proxy
chooses a sticky control/candidate arm from a server-owned session identity, generates a
server-owned request reference and persists request-to-experiment/release/deployment attribution
before forwarding the request.

Feedback processing:

~~~text
product request
 -> sticky control/candidate route
 -> persist server-owned request attribution
 -> return request reference
 -> validate submitted feedback
 -> resolve request reference to actual release/deployment/experiment
 -> persist immutable UserFeedback
 -> include attributable feedback in the next EvidenceWindow
 -> trigger diagnosis when policy threshold is met
~~~

A control request can be bound directly to a ReleasedVersion. A pre-promotion candidate request is
bound to its candidate deployment and experiment even though no release exists yet. After that
deployment is promoted, evidence queries can include the earlier feedback by deployment identity.

Client-reported release/deployment IDs are corroborating claims only and are rejected when they
conflict with server-owned attribution. Free-text feedback is evidence, not an executable prompt.

The EvidenceBundle sent to Codex clearly separates:

- system-owned objective/constraints;
- verified telemetry;
- user-supplied untrusted text;
- current source facts;
- previous failed attempts.

## 14. Runtime observability

Autonomous Development emits OpenTelemetry traces and Prometheus metrics.

Trace boundaries include:

- cycle;
- Codex turn;
- verification run;
- build;
- deployment;
- canary stage;
- promotion/rollback.

Core metrics include:

- cycle duration/status;
- Codex attempts/time;
- gate pass/fail counts;
- build duration/failures;
- deployment recovery count;
- canary stage duration;
- rollback count;
- promotion count;
- feedback ingestion/attribution rate;
- evidence freshness;
- candidate/control request/error/latency aggregates.

Logs must never include secrets or full untrusted user content by default. Events use IDs/digests and protected references.

## 15. Reliability design

V1 reliability is based on the following enforceable properties:

1. durable workflow state in DBOS backed by PostgreSQL for local deployment;
2. idempotent workflow IDs and provider operation IDs;
3. explicit state versions / optimistic concurrency for domain transitions;
4. independent reconciliation after ambiguous external effects;
5. no in-memory-only timers for development/canary/soak state;
6. immutable evidence and verification history;
7. artifact-by-digest release identity;
8. baseline kept available until candidate soak completes;
9. bounded retries with timeout and maximum recovery attempts;
10. crash/restart tests at every effect boundary;
11. database backup plus isolated restore drill before local deployment acceptance;
12. fail closed when evidence, credentials, providers or quality gates are unavailable.

A provider outage may delay the loop; it must not cause a false promotion.

## 16. V1 adapters

Implemented concrete runtime adapters:

### CodexProvider
Local Codex App Server JSON-RPC with bounded workspace read/write policy and durable thread journal.

### RepositoryProvider
Git CLI + git worktree, including candidate provenance, source fast-forward, rollback and terminal
cleanup.

### Quality gates
Registered command gates plus k6 performance execution.

### BuildProvider
Docker CLI / BuildKit with source-tree identity, Syft SBOM and Grype image scanning.

### DeploymentProvider
Local Docker CLI with deterministic deployment identity, reconciliation and loopback port binding.

### TrafficProvider
AtomicFileTrafficDirector plus the built-in FastAPI loopback canary proxy and ProxyCanaryObserver.

### TelemetryProvider
Prometheus query API with immutable local evidence records.

### FeedbackProvider
Local FastAPI ingestion backed by PostgreSQL request attribution and UserFeedback persistence.

### Persistence
PostgreSQL repositories plus DBOS PostgreSQL system state.

GitHub Actions is the CI/security surface for this repository, but the V1 local autonomous cycle
does not depend on a GitHub CI provider or remote source mutation.

## 17. Data storage

Local production V1 uses one PostgreSQL instance/container with separate logical ownership for:

- application/domain records;
- DBOS workflow state.

Migrations are Alembic-owned.

SQLite may be used only for focused development/unit tests where the test explicitly does not claim PostgreSQL behavior.

All critical migration tests include PostgreSQL upgrade/downgrade smoke.

## 18. Local deployment topology

Implemented topology:

~~~text
Windows host
|
+-- Codex CLI / App Server
|
+-- autonomous-development API/worker :8765
|      |
|      +-- /product built-in canary proxy
|      +-- PostgreSQL application state
|      +-- DBOS PostgreSQL system state
|      +-- Docker CLI
|      +-- git CLI
|      +-- k6 / Syft / Grype
|
+-- target baseline container(s) on loopback ports
+-- target candidate container(s) on loopback ports
|
+-- existing observability stack
       Prometheus :19090
       OTel / Grafana / Loki / Tempo as host-owned services where configured
~~~

The control plane does not require Traefik or Docker Compose for progressive delivery. Traffic
routing state is written atomically by the orchestrator and consumed by the mounted loopback
FastAPI proxy. Prometheus remains externally owned and is queried over loopback.

## 19. Security boundaries

- Codex receives no production/deployment secrets.
- Codex repository reads are restricted to the bounded worktree plus platform-default read roots;
  implementation writes are restricted to that worktree.
- Codex implementation mode has network access disabled.
- Host-global package installation is forbidden.
- Deployment credentials and Docker authority remain on the orchestrator/provider side.
- User feedback and target repository instructions are untrusted data and cannot widen policy.
- autonomous-development.toml is system-owned and excluded from autonomous mutation.
- Canary request attribution is persisted before forwarding when attribution is enabled; failure
  to persist attribution fails the proxy request closed.
- Docker socket access is not delegated to Codex as release authority.
- Every external command has a timeout and bounded execution contract.
- Public network exposure is not required for V1.

## 20. V1 development phases

These phases are implementation order, not separate product versions.

### Phase 0 — Repository foundation

Deliver:

- pyproject/uv.lock;
- package/layer skeleton;
- architecture contracts;
- CI/security/quality workflows;
- docs/AGENTS project instructions;
- typed config;
- PostgreSQL migration baseline.

Exit criteria:

- clean CI;
- architecture gate active;
- no runtime behavior yet.

### Phase 1 — Pure domain/state machine

Deliver all domain objects, transition rules and counterexample tests without external adapters.

Exit criteria:

- complete transition table;
- Hypothesis/state-machine tests;
- stale transition rejection;
- promotion cannot be constructed without evidence.

### Phase 2 — Durable orchestration

Add DBOS workflows, Postgres persistence, restart/recovery and idempotency.

Exit criteria:

- injected process crash resumes from durable boundary;
- duplicate trigger does not duplicate a cycle/effect;
- terminal workflows remain stable after restart;
- backup/restore drill works.

### Phase 3 — Codex + isolated repository development

Add Codex App Server and git-worktree adapters.

Exit criteria:

- requirement -> Codex -> bounded patch in isolated worktree;
- Codex cannot write outside workspace;
- restart can resume/reconcile the development attempt;
- no direct main checkout mutation.

### Phase 4 — Verification/quality pipeline

Add static, tests, security, Sonar, Codex review, artifact build and provenance.

Exit criteria:

- intentionally violating fixtures are rejected by every gate;
- valid fixture passes;
- scorer/gate tests prove gates are not vacuous;
- artifact digest is linked to source and scan evidence.

### Phase 5 — Local deploy + performance

Add Docker candidate deployment, health/readiness, k6 gate and independent observation.

Exit criteria:

- good candidate deploys;
- broken startup rejected;
- injected latency regression rejected;
- baseline remains available.

### Phase 6 — Progressive delivery

Add built-in weighted loopback routing, stage ledger, Prometheus queries and rollback.

Exit criteria:

- 10 -> 50 -> 100 progression works with sufficient evidence;
- hard regression rolls traffic back;
- crash during traffic shift reconciles actual weight before retry;
- previous release remains restorable.

### Phase 7 — Feedback and autonomous iteration

Add feedback ingestion, attribution, evidence windows, Codex diagnosis and subsequent automatic cycle trigger.

Exit criteria:

- user feedback is bound to the server-observed routed release/deployment, including pre-promotion candidate traffic;
- a seeded product defect causes feedback/telemetry;
- system diagnoses, creates a bounded change, implements, verifies, canaries and promotes without a human choosing the code change;
- malicious feedback text cannot escape the objective or mutation boundary.

### Phase 8 — Full-system acceptance and local deployment readiness

Run multi-cycle scenario and failure injection.

Required demonstration:

~~~text
Requirement
 -> V1 product
 -> deploy
 -> user interaction
 -> captured attributable defect feedback
 -> autonomous diagnosis
 -> autonomous Codex patch
 -> all quality/performance gates
 -> 10/50/100 canary
 -> promoted version
 -> second feedback cycle
~~~

Also demonstrate:

- API/worker restart during Codex wait;
- restart during build;
- restart during canary;
- lost acknowledgement around deployment/traffic update;
- candidate health regression;
- performance regression;
- security scan failure;
- test regression;
- stale feedback;
- insufficient canary evidence;
- automatic rollback;
- Postgres backup and isolated restore.

Only after these pass is V1 eligible for local deployment.

## 21. V1 acceptance definition

V1 is complete only when all statements below are true:

1. No human writes the target product change in the acceptance scenario.
2. No human selects the concrete fix after feedback.
3. The objective and safety/performance bounds remain human-owned.
4. Codex performs engineering but cannot promote/deploy by itself.
5. Every promoted release has reconstructable source, artifact, test, security, performance, canary and feedback evidence.
6. Restarting the control plane cannot lose or duplicate a development cycle.
7. Ambiguous side effects are reconciled before retry.
8. A known-bad candidate is automatically rejected/rolled back.
9. A known-good candidate can progress automatically to 100% and complete soak.
10. The previous good release remains a real rollback target until closure.
11. Code quality architecture rules are machine-enforced.
12. Performance regression is a blocking gate.
13. User feedback is server-attributed to the actual routed release/deployment/experiment and treated as untrusted evidence.
14. The entire demonstration can be repeated from a clean checkout and declared runtime dependencies.

If any of these are missing, the system is an automation prototype, not the V1 autonomous-development closed loop.

## 22. Frozen decisions for V1

The following choices are frozen for the implemented V1:

- language: Python 3.12+;
- dependency manager: uv with committed uv.lock and frozen CI sync;
- durable workflow: DBOS;
- durable store: PostgreSQL in local deployment;
- engineering executor: installed Codex through App Server;
- source isolation: git worktree;
- source authority: local clean default branch with exact commit/tree identity;
- repository CI/security surface: GitHub Actions;
- artifact: OCI/Docker image by digest;
- local runtime: Docker Desktop / Docker CLI;
- progressive traffic: built-in loopback FastAPI canary proxy + atomic route state;
- performance: k6;
- telemetry evidence: Prometheus query API;
- quality: Ruff, mypy, pytest, Hypothesis and Import Linter plus target-owned gates;
- security: Gitleaks, OSV-Scanner, Syft, Grype, actionlint and zizmor;
- deployment scope: one workstation, one registered bounded target at a time;
- promotion: deterministic multi-gate release controller;
- no direct dependency on prior personal agent repositories.

Implementation revision note: the original design named Traefik and a remote GitHub source/PR path.
The concrete V1 only needs local loopback progressive delivery and server-owned request
attribution, and its source lifecycle is deliberately local-first. The built-in proxy and local
fast-forward/rollback model reduce external state without changing the domain semantics of
Experiment, ReleaseDecision or rollback. GitHub remains the repository CI/security surface.

A future change to a frozen decision requires a concrete implementation-blocking fact, replacement
analysis and an explicit design revision before dependent implementation proceeds.
