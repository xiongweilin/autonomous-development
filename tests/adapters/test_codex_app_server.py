from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from autonomous_development.adapters.codex_app_server.client import CodexAppServer
from autonomous_development.ports.codex import (
    CodexProviderError,
    CodexSandbox,
    CodexTurnRequest,
)


def write_fake_server(path: Path, *, sleep_before_output: float = 0.0) -> Path:
    script = path / "fake_codex.py"
    script.write_text(
        f"""
import json
import sys
import time

SLEEP = {sleep_before_output!r}

def emit(value):
    print(json.dumps(value), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if SLEEP:
        time.sleep(SLEEP)
        SLEEP = 0
    if method == "initialize":
        emit({{"id": message["id"], "result": {{}}}})
    elif method == "initialized":
        pass
    elif method in ("thread/start", "thread/resume"):
        if method == "thread/start":
            params = message["params"]
            assert params["approvalPolicy"] == "never"
            assert params["sandbox"] == "workspaceWrite"
        emit({{"id": message["id"], "result": {{"thread": {{"id": "thr-test"}}}}}})
    elif method == "turn/start":
        params = message["params"]
        policy = params["sandboxPolicy"]
        assert policy["type"] == "workspaceWrite"
        assert policy["networkAccess"] is False
        emit({{"id": message["id"], "result": {{"turn": {{"id": "turn-test"}}}}}})
        emit({{
            "method": "item/completed",
            "params": {{"item": {{"type": "agentMessage", "text": "done"}}}},
        }})
        emit({{
            "method": "turn/completed",
            "params": {{"turn": {{"id": "turn-test", "status": "completed"}}}},
        }})
""",
        encoding="utf-8",
    )
    return script


def test_app_server_handshake_and_bounded_turn(tmp_path) -> None:
    fake = write_fake_server(tmp_path)
    provider = CodexAppServer(command=(sys.executable, str(fake)))
    result = provider.run_turn(
        CodexTurnRequest(
            prompt="change the code",
            cwd=tmp_path,
            sandbox=CodexSandbox.WORKSPACE_WRITE,
            timeout_seconds=5,
        )
    )

    assert result.thread_id == "thr-test"
    assert result.turn_id == "turn-test"
    assert result.completed
    assert result.agent_messages == ("done",)


def test_app_server_timeout_is_wall_clock_bounded(tmp_path) -> None:
    fake = write_fake_server(tmp_path, sleep_before_output=5)
    provider = CodexAppServer(command=(sys.executable, str(fake)))
    started = time.monotonic()

    with pytest.raises(CodexProviderError, match="timed out"):
        provider.run_turn(
            CodexTurnRequest(
                prompt="hang",
                cwd=tmp_path,
                sandbox=CodexSandbox.WORKSPACE_WRITE,
                timeout_seconds=1,
            )
        )

    assert time.monotonic() - started < 4
