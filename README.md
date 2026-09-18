# autonomous-development

Local-first autonomous software development and product-evolution control plane.

The system owns the closed loop:

```text
requirement
  -> diagnose / plan
  -> build
  -> verify
  -> deploy candidate
  -> user traffic
  -> telemetry / feedback
  -> diagnose
  -> modify
  -> test / evaluate
  -> canary
  -> promote or rollback
  -> repeat
```

V1 deliberately does **not** implement a coding agent. Codex is the engineering executor. This repository owns the durable development lifecycle, evidence, quality gates, progressive delivery, feedback attribution, promotion and rollback decisions around Codex.

## V1 design

- Canonical V1 architecture, semantics, lifecycle and acceptance criteria: [docs/v1-design.md](docs/v1-design.md)
- Local runtime dependencies and deployment prerequisites: [docs/v1-runtime-dependencies.md](docs/v1-runtime-dependencies.md)
- External research basis and adopted/rejected ideas: [docs/v1-research-basis.md](docs/v1-research-basis.md)

## V1 boundary

V1 is a standalone bounded context. Its core package must not import or require:

- `agent-kernel`
- `meta-controller`
- `administrative-orchestrator`

Those projects may inspire design decisions, and future adapters may integrate with them, but they do not define this repository's domain semantics.

V1 targets one registered software/Agent product at a time on one local Windows workstation with Docker Desktop. The product goal is human-owned; implementation and iterative improvement inside that goal may run autonomously.

No implementation or local deployment is part of the design-freeze commit.
