from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from autonomous_development.ports.codex import (
    CodexEvent,
    CodexProvider,
    CodexProviderError,
    CodexTurnRequest,
    CodexTurnResult,
)


class CodexExecProvider(CodexProvider):
    """Run a bounded implementation turn through the non-interactive Codex CLI."""

    def __init__(
        self,
        *,
        command: Sequence[str] = ("codex",),
        thread_journal_root: Path | None = None,
    ) -> None:
        if not command:
            raise ValueError("Codex CLI command must be non-empty")
        if thread_journal_root is not None and not thread_journal_root.is_absolute():
            raise ValueError("Codex thread journal root must be absolute")
        self._command = tuple(command)
        self._thread_journal_root = thread_journal_root

    def run_turn(self, request: CodexTurnRequest) -> CodexTurnResult:
        if request.output_schema is not None:
            raise CodexProviderError("Codex CLI provider does not support output schemas")
        thread_id = self._resolve_thread_id(request)
        args = [*self._command, "exec"]
        if thread_id is None:
            args.extend(("--sandbox", request.sandbox.value))
        else:
            args.append("resume")
        if request.model:
            args.extend(("--model", request.model))
        args.extend(("--skip-git-repo-check", "--json"))
        if thread_id is not None:
            args.append(thread_id)
        args.append(request.prompt)

        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        existing_pytest_options = environment.get("PYTEST_ADDOPTS", "").split()
        if (
            "-p" not in existing_pytest_options
            or "no:cacheprovider" not in existing_pytest_options
        ):
            environment["PYTEST_ADDOPTS"] = " ".join(
                (*existing_pytest_options, "-p", "no:cacheprovider")
            )
        process = subprocess.Popen(
            args,
            cwd=request.cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            stdout, _stderr = process.communicate(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _terminate(process)
            raise CodexProviderError("Codex CLI turn timed out") from exc

        if process.returncode != 0:
            raise CodexProviderError(f"Codex CLI exited with code {process.returncode}")
        return self._result(stdout, request, thread_id)

    def _result(
        self,
        stdout: str,
        request: CodexTurnRequest,
        requested_thread_id: str | None,
    ) -> CodexTurnResult:
        events: list[CodexEvent] = []
        agent_messages: list[str] = []
        thread_id = requested_thread_id
        completed = False
        for line in stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            event_type = payload.get("type")
            if not isinstance(event_type, str):
                continue
            events.append(CodexEvent(method=event_type, params=dict(payload)))
            if event_type == "thread.started":
                started = payload.get("thread_id")
                if isinstance(started, str) and started:
                    thread_id = started
                    self._record_thread(request.resume_key, started)
            elif event_type == "item.completed":
                item = payload.get("item")
                if isinstance(item, Mapping) and item.get("type") in {
                    "agent_message",
                    "agentMessage",
                }:
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        agent_messages.append(text)
            elif event_type == "turn.completed":
                completed = True

        if thread_id is None:
            raise CodexProviderError("Codex CLI did not return a thread identity")
        if not completed:
            raise CodexProviderError("Codex CLI did not complete the turn")
        return CodexTurnResult(
            thread_id=thread_id,
            turn_id=f"{thread_id}:turn",
            status="completed",
            events=tuple(events),
            agent_messages=tuple(agent_messages),
        )

    def _resolve_thread_id(self, request: CodexTurnRequest) -> str | None:
        journaled = self._load_thread(request.resume_key)
        if (
            request.thread_id is not None
            and journaled is not None
            and request.thread_id != journaled
        ):
            raise CodexProviderError(
                "explicit Codex thread id conflicts with durable thread journal"
            )
        return request.thread_id or journaled

    def _load_thread(self, resume_key: str | None) -> str | None:
        path = self._journal_path(resume_key)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexProviderError("Codex thread journal is unreadable") from exc
        if not isinstance(payload, dict):
            raise CodexProviderError("Codex thread journal is malformed")
        thread_id = payload.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexProviderError("Codex thread journal thread_id is invalid")
        return thread_id

    def _record_thread(self, resume_key: str | None, thread_id: str) -> None:
        path = self._journal_path(resume_key)
        if path is None:
            return
        existing = self._load_thread(resume_key)
        if existing is not None:
            if existing != thread_id:
                raise CodexProviderError(
                    "Codex resume key is already bound to a different thread"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{id(self)}.tmp")
        try:
            temporary.write_text(
                json.dumps({"thread_id": thread_id}, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise CodexProviderError("Codex thread journal could not be persisted") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _journal_path(self, resume_key: str | None) -> Path | None:
        if resume_key is None or self._thread_journal_root is None:
            return None
        digest = hashlib.sha256(resume_key.encode("utf-8")).hexdigest()
        return self._thread_journal_root / "exec" / f"{digest}.json"


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
