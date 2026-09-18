# V1 Research Basis

Status: evidence/rationale document  
Last reviewed: 2026-09-18

This document records external information that materially affected V1. It does not redefine V1 semantics; docs/v1-design.md is authoritative.

## 1. Autonomous coding harness lessons

### OpenAI — Harness engineering: leveraging Codex in an agent-first world

Source:
https://openai.com/index/harness-engineering/

Observed lessons:

- a real product was built with Codex-generated application code, tests, CI, docs, observability and tooling;
- repository/environment legibility was a major multiplier;
- isolated per-worktree application instances let Codex reproduce and validate changes;
- logs, metrics and traces were made directly legible to agents;
- architectural invariants were mechanically enforced rather than left as prose;
- recurring automated cleanup was needed to limit agent-generated architectural entropy;
- mature Codex workflows could reproduce a bug, implement, validate, review, remediate CI and merge, but human product prioritization/feedback translation remained a higher-level responsibility.

V1 adoption:

- reuse Codex rather than implement a coding agent;
- isolate every candidate with git worktree;
- make verification, logs, metrics and traces machine-readable;
- encode architecture in Import Linter/CI;
- treat code-health/cleanup as a quality responsibility rather than prompt advice.

V1 intentionally goes one step beyond the cited system in a bounded environment: feedback-to-change selection is part of the autonomous loop, while the top-level product objective remains human-owned.

### Anthropic — Effective harnesses for long-running agents

Source:
https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

Observed lessons:

- compaction alone is not sufficient for long application builds;
- long work benefits from decomposition and explicit durable handoff artifacts;
- sessions should make incremental progress and leave state that another session can understand.

V1 adoption:

- durable cycle/candidate/evidence objects live outside model context;
- Codex thread IDs are references, not the source of lifecycle truth;
- every phase has explicit artifacts/receipts so a new Codex turn or process can continue.

### Anthropic — Harness design for long-running application development

Source:
https://www.anthropic.com/engineering/harness-design-long-running-apps

Observed lesson:

- planner/generator/evaluator separation can improve long-running application work.

V1 adoption:

- preserve semantic separation between Diagnosis, Codex implementation and Evaluation;
- do not build three new agent runtimes. Codex performs model-backed work; deterministic gates remain separate evaluators.

## 2. Codex integration

### OpenAI — Codex App Server

Source:
https://developers.openai.com/docs/app-server

Relevant capabilities:

- start/resume/fork persisted threads;
- stream turn/tool/file-change events;
- interrupt turns;
- run review mode;
- control cwd, sandbox and approval policy;
- receive structured lifecycle/error information.

V1 decision:

- App Server JSON-RPC is the primary Codex adapter;
- codex exec is only a compatibility/smoke adapter;
- Codex threads are persisted references but V1 workflow state remains in PostgreSQL/DBOS.

### OpenAI — Codex non-interactive mode and sandbox/security guidance

Sources:
https://developers.openai.com/docs/non-interactive-mode
https://developers.openai.com/docs/agent-approvals-security
https://developers.openai.com/docs/sandboxing

Observed lessons:

- workspace-write is the intended bounded local automation mode;
- network access is separate and can remain disabled;
- danger-full-access is for already-isolated environments and is not required for normal automation;
- structured output schemas are supported in non-interactive flows.

V1 adoption:

- diagnosis is read-only;
- implementation uses workspace-write in the candidate worktree;
- network off by default;
- Codex does not get release/deployment credentials;
- structured schemas are used for Diagnosis and review output.

## 3. Durable execution

### DBOS — Python workflows and recovery

Sources:
https://docs.dbos.dev/python/tutorials/workflow-tutorial
https://docs.dbos.dev/production/workflow-recovery
https://docs.dbos.dev/python/programming-guide

Relevant properties:

- workflows checkpoint state and recover after process interruption;
- workflow IDs can act as idempotency keys;
- durable sleep supports long waits;
- queues support background/concurrent work;
- completed workflow steps are not rerun after completion, while effectful external calls still need idempotency/reconciliation discipline;
- PostgreSQL is recommended for production deployment.

V1 decision:

- DBOS is the V1 durable workflow substrate;
- PostgreSQL is required for accepted local deployment;
- external side effects still carry V1 operation IDs and reconciliation logic rather than assuming the workflow engine makes external systems exactly-once.

### Temporal

Source:
https://docs.temporal.io/

Temporal provides mature durable execution and long-lived workflow recovery.

V1 rejection:

- no technical rejection of Temporal;
- it adds a larger runtime surface than needed for the first single-workstation Python implementation;
- V1 uses DBOS and does not attempt to build an abstraction layer solely to hide that choice.

## 4. Evaluation and production feedback

### Anthropic — Demystifying evals for AI agents

Source:
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

Observed lessons:

- automated evals, production monitoring, user feedback, A/B tests and human review cover different failure classes;
- no single layer is sufficient;
- regression suites and quality suites should be distinguished;
- production signals are needed because offline evals do not fully match real usage.

V1 adoption:

- layered gates: unit/contract -> regression/eval -> performance -> live canary -> post-promotion soak;
- explicit user feedback is preserved as its own evidence type;
- no single score decides release;
- critical criteria require product-grounded/deterministic oracles.

## 5. Progressive delivery

### Google SRE — Canarying Releases

Source:
https://sre.google/workbook/canarying-releases/

Observed lessons:

- a canary is partial, time-limited exposure evaluated before broader rollout;
- builds/tests/deployments should be automated and reproducible;
- release changes should be small;
- canary size and duration must be sufficient to be representative;
- candidate and control should be compared concurrently when possible;
- metrics should be attributable and close to actual user impact;
- rollback should be cheap and automated.

V1 adoption:

- 10% -> 50% -> 100% default stages;
- minimum duration + minimum sample count;
- control and candidate coexist;
- hard reliability/latency/product metrics drive rollback;
- insufficient evidence means HOLD, not PASS;
- previous artifact remains available through soak.

### Argo Rollouts

Source:
https://argoproj.github.io/rollouts/

Argo Rollouts demonstrates a mature interface for canary/blue-green rollout, metric analysis, promotion and rollback.

V1 adoption/rejection:

- adopt the conceptual separation of rollout stages, analysis and promotion;
- do not require Kubernetes in V1;
- keep TrafficProvider/DeploymentProvider boundaries so a future Argo adapter can implement the same semantics.

### Traefik weighted services

Source:
https://doc.traefik.io/traefik/master/routing/services/

Relevant capability:

- weighted services can distribute traffic between multiple services with controlled weights.

V1 decision:

- use Traefik in a local Docker container as the first TrafficProvider implementation.

### OpenFeature

Source:
https://openfeature.dev/specification/

OpenFeature defines a vendor-neutral feature-flag provider abstraction and supports use cases such as canary release and A/B testing.

V1 decision:

- no separate feature-flag platform is needed for the first service-level loop;
- preserve a future FeatureFlagProvider extension point instead of baking product-specific flag semantics into the V1 domain.

## 6. Performance

### Grafana k6 thresholds

Source:
https://grafana.com/docs/k6/latest/using-k6/thresholds/

Relevant capability:

- thresholds are machine-pass/fail criteria over metrics such as error rate and latency;
- failed thresholds make load tests fail and are suitable for automation.

V1 decision:

- k6 is the pre-canary performance gate;
- targets define both absolute SLO thresholds and baseline-relative regression limits.

## 7. Observability

### OpenTelemetry Python

Source:
https://opentelemetry.io/docs/languages/python/

Relevant capability:

- standard APIs/SDKs for traces and metrics;
- instrumentation ecosystem for Python/FastAPI/httpx.

V1 decision:

- emit OTel traces to the owner's existing OTel collector;
- emit Prometheus-compatible metrics;
- domain objects refer to telemetry evidence, while the shared observability infrastructure remains an external owner.

## 8. Code architecture and quality

### Import Linter

Source:
https://import-linter.readthedocs.io/en/latest/

Relevant capability:

- mechanically enforce forbidden, protected, layered, independence and acyclic import contracts.

V1 decision:

- use Import Linter to make the domain/application/ports/adapters dependency direction executable in CI.

### Sonar quality gates

Source:
https://docs.sonarsource.com/sonarqube-server/quality-standards-administration/managing-quality-gates/introduction-to-quality-gates

Relevant current recommended gate concepts:

- no new issues;
- security-hotspot review;
- new-code coverage threshold;
- new-code duplication threshold.

V1 decision:

- use SonarCloud for Autonomous Development itself and as an optional-but-first-class target gate when configured;
- local deterministic gates remain mandatory even if Sonar is unavailable.

### Hypothesis

Source:
https://hypothesis.readthedocs.io/en/latest/

Relevant capability:

- property-based input generation and shrinking to find edge cases not manually enumerated.

V1 decision:

- apply Hypothesis to state transitions, idempotency keys, stale-version rejection and other core invariants.

## 9. Security and supply chain

### OSV-Scanner

Source:
https://google.github.io/osv-scanner/usage/

Relevant capability:

- scan source/lockfiles and container images against known vulnerability data.

### Gitleaks

Source:
https://github.com/gitleaks/gitleaks

Relevant capability:

- scan git history/files/stdin for leaked credentials.

### GitHub Actions secure-use guidance

Source:
https://docs.github.com/en/actions/reference/security/secure-use

Relevant guidance:

- pin third-party GitHub Actions to full-length commit SHAs for immutable action identity.

### GitHub dependency review and artifact attestations

Sources:
https://docs.github.com/en/code-security/concepts/supply-chain-security/dependency-review
https://docs.github.com/en/actions/concepts/security/artifact-attestations

V1 adoption:

- OSV + Gitleaks mandatory;
- Syft SBOM + Grype image scan using tools already in the workstation baseline;
- Actions pinned by SHA;
- dependency review used where supported;
- artifact digest/source/build provenance recorded locally;
- GitHub artifact attestations may be added where the target's repository/registry model makes them useful, but local V1 correctness does not depend on a remote registry.

## 10. Research conclusion

The repeated external pattern is not "build a smarter coding agent."

It is:

~~~text
strong existing coding agent
+ legible isolated environment
+ durable state outside model context
+ mechanical architecture and quality constraints
+ layered evals
+ reproducible artifact identity
+ progressive real-traffic exposure
+ observable rollback
+ attributable production/user feedback
= reliable autonomous development loop
~~~

That is the V1 architecture selected in docs/v1-design.md.
