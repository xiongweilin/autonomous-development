from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autonomous_development.ports.codex import (
    CodexEvent,
    CodexProvider,
    CodexProviderError,
    CodexSandbox,
    CodexTurnRequest,
    CodexTurnResult,
)


class CodexAppServer(CodexProvider):
    """Run one bounded Codex turn per app-server process.

    Codex thread identity is durable; app-server process identity deliberately is not.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] = ("codex", "app-server"),
        client_name: str = "autonomous_development",
        client_version: str = "0.1.0",
    ) -> None:
        if not command:
            raise ValueError("Codex app-server command must be non-empty")
        self._command = tuple(command)
        self._client_name = client_name
        self._client_version = client_version

    def run_turn(self, request: CodexTurnRequest) -> CodexTurnResult:
        process = subprocess.Popen(
            self._command,
            cwd=request.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise CodexProviderError("Codex app-server pipes are unavailable")

        messages: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(
            target=_read_lines,
            args=(process.stdout, messages),
            name="codex-app-server-reader",
            daemon=True,
        )
        reader.start()
        deadline = time.monotonic() + request.timeout_seconds
        events: list[CodexEvent] = []
        try:
            self._send(
                process.stdin,
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": self._client_name,
                            "title": "Autonomous Development",
                            "version": self._client_version,
                        }
                    },
                },
            )
            self._response(messages, expected_id=1, deadline=deadline, events=events)
            self._send(process.stdin, {"method": "initialized", "params": {}})

            method = "thread/resume" if request.thread_id else "thread/start"
            params: dict[str, object] = {}
            if request.thread_id:
                params["threadId"] = request.thread_id
            if request.model:
                params["model"] = request.model
            if not request.thread_id:
                params.update(
                    {
                        "cwd": str(request.cwd),
                        "approvalPolicy": "never",
                        "sandbox": request.sandbox.value,
                        "serviceName": "autonomous-development",
                    }
                )
            self._send(process.stdin, {"method": method, "id": 2, "params": params})
            thread_response = self._response(
                messages,
                expected_id=2,
                deadline=deadline,
                events=events,
            )
            thread = _mapping(thread_response.get("thread"), "thread")
            thread_id = _string(thread.get("id"), "thread.id")

            turn_params: dict[str, object] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": request.prompt}],
                "cwd": str(request.cwd),
                "approvalPolicy": "never",
                "sandboxPolicy": _sandbox_policy(request.sandbox, request.cwd),
            }
            if request.model:
                turn_params["model"] = request.model
            if request.output_schema is not None:
                turn_params["outputSchema"] = dict(request.output_schema)
            self._send(
                process.stdin,
                {"method": "turn/start", "id": 3, "params": turn_params},
            )
            turn_response = self._response(
                messages,
                expected_id=3,
                deadline=deadline,
                events=events,
            )
            turn = _mapping(turn_response.get("turn"), "turn")
            turn_id = _string(turn.get("id"), "turn.id")

            status, agent_messages = self._await_completion(
                messages,
                thread_id=thread_id,
                turn_id=turn_id,
                deadline=deadline,
                events=events,
            )
            return CodexTurnResult(
                thread_id=thread_id,
                turn_id=turn_id,
                status=status,
                events=tuple(events),
                agent_messages=tuple(agent_messages),
            )
        except (BrokenPipeError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise CodexProviderError(str(exc)) from exc
        finally:
            _terminate(process)
            reader.join(timeout=1)

    def _await_completion(
        self,
        messages: queue.Queue[str | None],
        *,
        thread_id: str,
        turn_id: str,
        deadline: float,
        events: list[CodexEvent],
    ) -> tuple[str, list[str]]:
        agent_messages: list[str] = []
        while True:
            message = self._read(messages, deadline)
            method = message.get("method")
            if not isinstance(method, str):
                continue
            params = _mapping(message.get("params", {}), "notification params")
            events.append(CodexEvent(method=method, params=dict(params)))
            if method == "item/completed":
                item = params.get("item")
                if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        agent_messages.append(text)
            elif method == "turn/completed":
                completed = _mapping(params.get("turn"), "completed turn")
                completed_id = _string(completed.get("id"), "completed turn.id")
                if completed_id != turn_id:
                    continue
                status = _string(completed.get("status"), "completed turn.status")
                return status, agent_messages
            elif method == "turn/error":
                raise CodexProviderError(
                    f"Codex turn failed for thread {thread_id}: {params!r}"
                )

    def _response(
        self,
        messages: queue.Queue[str | None],
        *,
        expected_id: int,
        deadline: float,
        events: list[CodexEvent],
    ) -> Mapping[str, object]:
        while True:
            message = self._read(messages, deadline)
            if message.get("id") == expected_id:
                error = message.get("error")
                if error is not None:
                    raise CodexProviderError(f"Codex RPC error: {error!r}")
                return _mapping(message.get("result"), "RPC result")
            method = message.get("method")
            if isinstance(method, str):
                params = _mapping(message.get("params", {}), "notification params")
                events.append(CodexEvent(method=method, params=dict(params)))

    @staticmethod
    def _send(stdin: Any, message: Mapping[str, object]) -> None:
        stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        stdin.flush()

    @staticmethod
    def _read(
        messages: queue.Queue[str | None],
        deadline: float,
    ) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexProviderError("Codex turn timed out")
        try:
            line = messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise CodexProviderError("Codex turn timed out") from exc
        if line is None:
            raise CodexProviderError("Codex app-server closed stdout unexpectedly")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise CodexProviderError("Codex app-server emitted a non-object JSON message")
        return message


def _read_lines(stdout: Any, messages: queue.Queue[str | None]) -> None:
    try:
        for line in stdout:
            messages.put(line)
    finally:
        messages.put(None)


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _sandbox_policy(sandbox: CodexSandbox, cwd: Path) -> dict[str, object]:
    if sandbox is CodexSandbox.READ_ONLY:
        return {"type": "readOnly", "access": {"type": "fullAccess"}}
    return {
        "type": "workspaceWrite",
        "writableRoots": [str(cwd)],
        "networkAccess": False,
    }


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CodexProviderError(f"{field} must be an object")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CodexProviderError(f"{field} must be a non-empty string")
    return value
