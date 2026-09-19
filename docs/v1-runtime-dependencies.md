# V1 Runtime Dependencies and Local Environment

Status: V1 runtime dependency owner  
Last reviewed: 2026-09-19

This document separates durable documented environment facts from runtime health. It does not claim that a binary is healthy merely because a package list or RUNBOOK says it should exist.

## 1. Evidence sources inspected

The design used the current documents in xiongweilin/ratio and the current managed workstation baseline in xiongweilin/pc-dotfiles.

Relevant durable owners:

- ratio/RUNBOOK/环境重建清单.md — workstation restore boundary;
- ratio/RUNBOOK/环境运维手册.md — runtime checks;
- ratio/RUNBOOK/模型路由.md — Codex/LiteLLM routing boundary;
- ratio/RUNBOOK/Codex 恢复与接入.md — Codex/MCP recovery boundary;
- ratio/RUNBOOK/metratio.com 基础设施文档.md — Docker/observability topology;
- pc-dotfiles/packages/scoop-apps.txt — managed Scoop CLI baseline;
- pc-dotfiles/packages/python-tools.txt — managed global Python CLI baseline;
- pc-dotfiles/dot_local/bin/env-doctor.ps1 and cli-health.ps1 — runtime probes.

The pc-dotfiles package list was freshly synchronized on 2026-09-18. Even so, deployment must run live probes before treating the machine as ready.

## 2. Existing workstation capabilities V1 should reuse

### 2.1 Required host tools already in the managed baseline

Directly relevant entries in the managed Scoop baseline:

- pwsh;
- git;
- gh;
- uv;
- python;
- curl;
- jq;
- ripgrep;
- k6;
- sonar-scanner;
- gitleaks;
- osv-scanner;
- syft;
- grype;
- actionlint;
- zizmor;
- graphviz;
- 7zip.

The baseline also contains general compilers/runtimes such as Go, GCC, Rust and JDK, but V1 does not require them as control-plane dependencies.

### 2.2 Docker Desktop

ratio records Windows Docker Desktop with the WSL2 backend as part of the workstation topology.

V1 uses Docker for:

- PostgreSQL;
- target baseline/candidate containers;
- local integration/acceptance stacks.

Progressive delivery itself is implemented by the control plane's loopback FastAPI canary proxy
plus an atomic local route-state adapter. V1 does not require a separate proxy container.

V1 does not install or manage Docker Desktop.

### 2.3 Codex

Codex availability is explicitly confirmed by the owner.

V1 depends on the installed Codex harness and uses Codex App Server as the main engineering-executor protocol. V1 does not implement a replacement coding agent.

The existing local model gateway remains an indirect Codex concern. Autonomous Development does not call ports 4100/4101/4102 or depend on LiteLLM APIs directly.

### 2.4 Existing observability stack

ratio records a local observability stack owned outside this project:

- Grafana: local port 14200;
- Prometheus: local port 19090;
- Alertmanager: local port 19093;
- OpenTelemetry Collector OTLP: local ports 4317 and 4318;
- Loki;
- Tempo;
- Promtail.

Current durable topology is:

~~~text
application
 -> OTel Collector
 -> Tempo
 -> span/service metrics
 -> Prometheus
 -> Grafana

containers
 -> Promtail
 -> Loki
 -> Grafana
~~~

V1 emits OTLP and Prometheus-compatible metrics but does not own or duplicate this stack.

### 2.5 Existing secret/credential boundaries

V1 must reuse the existing credential ownership discipline:

- GitHub CLI authentication remains owned by gh/keyring;
- SonarCloud credential locator remains outside project files;
- Bitwarden / Windows Credential Manager store secrets;
- project/runtime configuration stores only locators or environment-variable names.

No secret value is committed to this repository or copied to ratio.

## 3. Runtime readiness probes required before local deployment

The final deployment procedure must prove current state with live commands, at minimum:

~~~powershell
cli-health.ps1
env-doctor.ps1

git --version
gh auth status --hostname github.com
uv --version
python --version
codex --version
docker version
docker compose version
k6 version
gitleaks version
osv-scanner --version
syft version
grype version
sonar-scanner --version
actionlint -version
zizmor --version

curl.exe -fsS http://127.0.0.1:19090/-/ready
curl.exe -fsS http://127.0.0.1:4318/
~~~

Exact OTLP readiness probing may differ from a normal HTTP GET; implementation must use the collector's actual configured health endpoint when available rather than assuming the OTLP receiver itself is a health endpoint.

The runtime check must distinguish:

- command present;
- command functional;
- external authentication functional;
- Docker daemon reachable;
- observability endpoint reachable.

A failure in an optional external service must not be relabeled as local tool absence.

## 4. Project Python dependencies to add with uv

These are project dependencies, not workstation-global installations.

### Runtime

- DBOS 2.x — durable workflows, queues, durable waits and recovery;
- FastAPI — local control/feedback API;
- Uvicorn — API server;
- Pydantic 2.x — domain/config/contract validation;
- pydantic-settings — environment configuration;
- SQLAlchemy 2.x — application persistence;
- Alembic — schema migrations;
- psycopg 3.x — PostgreSQL driver;
- httpx — provider HTTP clients;
- structlog — structured application logs;
- prometheus-client — runtime metrics;
- OpenTelemetry API/SDK;
- OTLP HTTP exporter;
- FastAPI/httpx OpenTelemetry instrumentation.

Exact transitive versions are committed in `uv.lock`. CI installs with `uv sync --frozen`
and verifies the lock with `uv lock --check`; lock drift is a build failure.

### Development/test

- pytest;
- pytest-asyncio;
- pytest-cov;
- Hypothesis;
- Ruff;
- mypy;
- Import Linter.

Target projects keep their own test/runtime dependencies. Autonomous Development must not inject its preferred Python stack into every target.

## 5. Local container dependencies

### PostgreSQL

Use PostgreSQL 17-alpine for the V1 local runtime.

Why:

- durable application state;
- DBOS production-grade backing database;
- existing workstation/project familiarity;
- supports restart/recovery and backup/restore acceptance.

The image must be pinned by digest in the accepted deployment definition.

### Built-in canary proxy

V1 uses the control plane's FastAPI application as the stable loopback product entrypoint. The
proxy is mounted at `/product` and reads route state written by
`AtomicFileTrafficDirector`.

Responsibilities:

- deterministic sticky control/candidate assignment by server-owned session identity;
- server-generated request references;
- durable request-to-experiment/release/deployment attribution in PostgreSQL;
- per-route-generation request/error/latency measurements;
- fail-closed behavior when route attribution cannot be persisted;
- explicit control restoration during rollback.

No Traefik container is required by the implemented V1 runtime.

## 6. Tools V1 deliberately does not require

### Node/npm

The workstation health scripts inspect Node/npm, but Autonomous Development does not require Node to drive Codex because the primary integration is the installed Codex App Server protocol.

A target project may of course require Node; that is target-owned.

### Kubernetes / kubectl / Helm / Argo Rollouts

Not required in V1.

The deployment domain keeps a ProgressiveDelivery/Traffic provider boundary so a later Kubernetes/Argo adapter can be added without changing release semantics.

### Temporal

Not required in V1.

DBOS is selected for the first local durable-workflow implementation because it is lightweight, Python-native, PostgreSQL-backed and already familiar in the owner's environment. DurableWorkflow is kept as an internal architectural seam; V1 does not build a second workflow abstraction framework merely to make DBOS swappable.

### Redis / message broker

Not required in V1.

DBOS/PostgreSQL provide the durable queue/workflow substrate needed by this scope.

### Separate feature-flag platform

Not required in V1.

V1 uses service-level progressive delivery through TrafficProvider. A future FeatureFlagProvider may implement OpenFeature-compatible behavior for in-process flags and A/B tests.

### Separate coding-agent SDK/framework

Not required.

Codex is the engineering executor.

### Browser automation as a platform dependency

Not required by the control plane in V1.

A target may register Playwright/browser tests as target verification commands. If later cross-target UI inspection becomes a platform responsibility, add a BrowserEvaluationProvider rather than baking one browser stack into domain semantics.

## 7. Interfaces reserved for later tools

The following ports should exist only when a V1 use case needs the seam; they need no second implementation in V1:

- CodexProvider;
- RepositoryProvider;
- CIProvider;
- BuildProvider;
- DeploymentProvider;
- TrafficProvider;
- TelemetryProvider;
- FeedbackProvider;
- SecretResolver;
- QualityGate.

Do not create speculative provider interfaces for every future technology.

Explicit future extension points documented but not implemented in V1:

- RemoteDeploymentProvider;
- ArtifactRegistryProvider;
- FeatureFlagProvider / OpenFeature adapter;
- Kubernetes/Argo progressive-delivery adapter;
- BrowserEvaluationProvider;
- advanced ExperimentAnalyzer;
- external agent-runtime adapter.

## 8. Host installation conclusion

Based on the durable workstation baseline, **V1 should not require a new host-level developer tool installation**.

Implementation needs:

1. new Python project dependencies resolved by uv;
2. a PostgreSQL runtime and the target product's baseline/candidate Docker images;
3. project-specific GitHub/Sonar configuration where enabled.

Before accepting that conclusion at deployment time, run the live readiness probes. If a required managed tool is missing, restore it through pc-dotfiles/Scoop rather than adding an ad-hoc installer to this repository.

## 9. Ownership rule

This repository owns only what it needs from those tools:

- command/protocol expectations;
- provider adapter contracts;
- readiness requirements;
- version compatibility declared in pyproject/lock/deployment files.

It does not become the owner of:

- pc-dotfiles;
- Codex user configuration;
- LiteLLM routing;
- Docker Desktop;
- the shared observability stack;
- GitHub authentication;
- Bitwarden/Credential Manager;
- SonarCloud account configuration.

That separation is required so a workstation recovery or tool upgrade does not silently rewrite Autonomous Development domain semantics.
