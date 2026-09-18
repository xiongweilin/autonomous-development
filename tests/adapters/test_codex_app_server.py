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


def write_fake_server(
    path: Path,
    *,
    sleep_once_on_turn_start: float = 0.0,
) -> tuple[Path, Path]:
    script = path / "fake_codex.py"
    log = path / "methods.log"
    marker = path / "slept.marker"
    script.write_text(
        f"""
import json
import pathlib
import sys
import time

SLEEP_TURN = {sleep_once_on_turn_start!r}
LOG = pathlib.Path({str(log)!r})
MARKER = pathlib.Path({str(marker)!r})

def emit(value):
    print(json.dumps(value), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(str(method) + "\\n")

    if method == "initialize":
        emit({{"id": message["id"], "result": {{}}}})
    elif method == "initialized":
        pass
    elif method in ("thread/start", "thread/resume"):
        if method == "thread/start":
            params = message["params"]
            assert params["approvalPolicy"] == "never"
            assert params["sandbox"] == "workspaceWrite"
        else:
            assert message["params"]["threadId"] == "thr-test"
        emit({{"id": message["id"], "result": {{"thread": {{"id": "thr-test"}}}}}})
    elif method == "turn/start":
        if SLEEP_TURN and not MARKER.exists():
            MARKER.write_text("slept", encoding="utf-8")
            time.sleep(SLEEP_TURN)
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
    return script, log


def request(tmp_path: Path, *, timeout_seconds: int = 5) -> CodexTurnRequest:
    return CodexTurnRequest(
        prompt="change the code",
        cwd=tmp_path,
        sandbox=CodexSandbox.WORKSPACE_WRITE,
        resume_key="cycle-1:implementation:1",
        timeout_seconds=timeout_seconds,
    )


def test_app_server_handshake_and_bounded_turn(tmp_path: Path) -> None:
    fake, _ = write_fake_server(tmp_path)
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


def test_thread_journal_resumes_after_process_dies_while_waiting(tmp_path: Path) -> None:
    fake, log = write_fake_server(tmp_path, sleep_once_on_turn_start=5)
    journal = (tmp_path / "thread-journal").resolve()
    first = CodexAppServer(
        command=(sys.executable, str(fake)),
        thread_journal_root=journal,
    )
    started = time.monotonic()

    with pytest.raises(CodexProviderError, match="timed out"):
        first.run_turn(request(tmp_path.resolve(), timeout_seconds=1))

    assert time.monotonic() - started < 4
    journal_files = tuple(journal.glob("*.json"))
    assert len(journal_files) == 1
    assert "thr-test" in journal_files[0].read_text(encoding="utf-8")

    restarted = CodexAppServer(
        command=(sys.executable, str(fake)),
        thread_journal_root=journal,
    )
    result = restarted.run_turn(request(tmp_path.resolve(), timeout_seconds=5))
    assert result.completed
    assert result.thread_id == "thr-test"

    methods = log.read_text(encoding="utf-8").splitlines()
    assert methods.count("thread/start") == 1
    assert methods.count("thread/resume") == 1


def test_app_server_timeout_is_wall_clock_bounded(tmp_path: Path) -> None:
    fake, _ = write_fake_server(tmp_path, sleep_once_on_turn_start=5)
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
