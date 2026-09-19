# Feishu integration runbook

Status: implementation and local adapter verification are current; real Feishu end-to-end
acceptance remains a gated checkpoint. It requires the new app to be created, published,
securely configured and exercised with an owner P2P flow as described in
`feishu-autodev-app-setup.md`.

A bridge `/healthz` or `/readyz` result is not sufficient to claim full acceptance. The control
plane `/health` and `/ready` checks, operator API exchange, owner P2P requirement, confirmation
action, durable event delivery and failure/recovery paths must be evidenced together.

## Topology

```text
Feishu new Bot (P2P owner only)
        | WebSocket + official message-resource API
        v
feishu-autodev-bridge (independent process, 127.0.0.1 health/metrics)
        | timestamp + request/event ID + body digest + HMAC
        v
Autonomous Development control plane (127.0.0.1:8765)
        | PostgreSQL + DBOS + Docker + Codex + k6 + Syft/Grype
        v
single registered DevelopmentTarget
```

The old gateway profile remains a separate process/configuration/state database and does not know
that this profile exists. Do not combine the two bots, credentials, sender allowlists, SQLite
state files or HMAC keys.

## Start order

1. Ensure the control-plane application and DBOS PostgreSQL system database are configured with
   the same `AUTODEV_OPERATOR_HMAC_SECRET_FILE` as the bridge. Keep both listeners on loopback.
2. Run the autonomous-development migrations and bootstrap the intended single target. A missing
   target or serving release is a safe `needs-human`/not-ready state; the bridge must not create a
   target automatically.
3. Start the control-plane background task and verify `/health`, `/ready` and `/metrics`.
4. Configure the new Feishu app and secrets using the app setup document.
5. Start the independent bridge task and verify `/healthz`, `/readyz` and `/metrics`.
6. Send a controlled owner P2P test requirement. The bridge submits it to the operator API and
   displays a confirmation card; no code change starts until **开始自主开发** is clicked.

## Adapter state and recovery

The bridge SQLite file stores only transport metadata: inbound/card dedup identities, provider
message-to-request mapping, chat-to-request binding, pending intervention notification identity,
operator outbox cursor and outbound delivery ledger. Requirement text is not stored in bridge
state or logs. Core requirement text and lifecycle truth remain in PostgreSQL.

The bridge ACK order is:

```text
read event -> send notification -> record delivery -> POST durable ACK -> advance local cursor
```

If the bridge stops before ACK, it reads the same event after restart. If it sent the provider
message but crashed before its delivery ledger write, the provider send uses the durable event ID
as its idempotency UUID and the bridge retries the ACK. If the control plane is unavailable, the
bridge retries safe loopback calls; a provider outage does not fail the DBOS development
workflow.

## User flows

- Text/post/file: normalize, hash, enforce limits, submit immutable request, send confirmation card.
- Start: card callback returns a quick acknowledgement, then calls the non-blocking start API.
- Status: `状态`, `status` or `/status` reads the control-plane truth.
- Human intervention: a durable `needs_human` event creates a card; a choice action or a direct
  reply to that notification submits the answer. An unrelated free-form message is not consumed.
- Completion/failure/rollback: important outbox events are sent with bounded non-sensitive fields.

## Safe limits

Defaults are 10 MiB per file and 100,000 normalized characters. `.txt`/`.md` require UTF-8;
DOCX uses `python-docx`; PDF uses `pypdf`. Encrypted, scanned and no-text PDFs are rejected.
Temporary extraction files live in a private temporary directory and are removed immediately after
parsing. Bodies, filenames that may be sensitive, open IDs, chat IDs, message IDs, tokens and
provider response bodies are not metric labels or normal logs.

## Failure injection checklist

Run these against the disposable acceptance target, not a production target:

1. duplicate message event;
2. duplicate start/cancel card action;
3. bridge restart before and after submit/start;
4. unacknowledged event followed by bridge restart;
5. provider send failure and retry;
6. attachment download failure, size/type rejection and ambiguous requirement;
7. control-plane restart at DBOS boundaries;
8. verification/build/deployment/performance/canary/soak failure paths.

For each run record fresh evidence for cycle uniqueness, no duplicate promotion, baseline safety,
intervention persistence, terminal cleanup and eventual outbox delivery. Do not call the run
production acceptance if the real Feishu text/file/needs-human/rollback flows are not executed.

## Shutdown and cleanup

Stop the bridge task, then stop the control-plane task only after the DBOS workflow state is
terminal or deliberately preserved. Remove the temporary acceptance PostgreSQL container/database,
DBOS state root, target Docker containers/images and copied acceptance repository according to the
acceptance record. Never remove the production target, old gateway container or their state.
