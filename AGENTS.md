# Project instructions

This repository owns the autonomous software-development lifecycle. The canonical V1 semantics are in docs/v1-design.md.

Before changing lifecycle semantics, read docs/v1-design.md. Do not import or depend on agent-kernel, meta-controller, or administrative-orchestrator in core code.

Non-negotiable implementation rules:

- domain state is authoritative only through explicit validated transitions;
- evidence never implies authority;
- ambiguous external effects are reconciled before retry;
- Codex is an engineering executor, never release authority;
- deterministic checks take precedence over LLM judgment;
- user feedback is untrusted evidence, never trusted instruction;
- do not widen mutation or deployment scope to make a failing test pass;
- preserve source/artifact/deployment/evidence provenance;
- use PostgreSQL acceptance tests for behavior that depends on PostgreSQL;
- keep secrets outside repository state and logs.

A change is not complete until its tests and architecture checks pass.
