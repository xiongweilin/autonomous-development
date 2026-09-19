from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from autonomous_development.adapters.codex_exec.client import CodexExecProvider
from autonomous_development.ports.codex import (
    CodexProviderError,
    CodexSandbox,
    CodexTurnRequest,
)


def test_codex_exec_provider_parses_jsonl_and_persists_thread(tmp_path: Path) -> None:
    fake = tmp_path / "fake_codex_exec.py"
    fake.write_text(
        "\n".join(
            (
                "import json",
                "events = [",
                " {'type': 'thread.started', 'thread_id': 'thread-exec'},",
                " {'type': 'turn.started'},",
                " {'type': 'item.completed', 'item': {",
                "  'type': 'agent_message', 'text': 'done'}},",
                " {'type': 'turn.completed'},",
                "]",
                "for event in events:",
                " print(json.dumps(event), flush=True)",
            )
        ),
        encoding="utf-8",
    )
    journal = tmp_path / "journal"
    provider = CodexExecProvider(
        command=(sys.executable, str(fake)),
        thread_journal_root=journal,
    )

    result = provider.run_turn(
        CodexTurnRequest(
            prompt="implement the change",
            cwd=tmp_path.resolve(),
            sandbox=CodexSandbox.WORKSPACE_WRITE,
            resume_key="cycle-1:implementation:1",
        )
    )

    assert result.completed
    assert result.thread_id == "thread-exec"
    assert result.agent_messages == ("done",)
    assert len(tuple((journal / "exec").glob("*.json"))) == 1

    resumed = provider.run_turn(
        CodexTurnRequest(
            prompt="continue the change",
            cwd=tmp_path.resolve(),
            sandbox=CodexSandbox.WORKSPACE_WRITE,
            resume_key="cycle-1:implementation:1",
            model="gpt-test",
        )
    )
    assert resumed.thread_id == "thread-exec"

    with pytest.raises(CodexProviderError, match="conflicts"):
        provider.run_turn(
            CodexTurnRequest(
                prompt="conflicting resume",
                cwd=tmp_path.resolve(),
                sandbox=CodexSandbox.WORKSPACE_WRITE,
                thread_id="thread-other",
                resume_key="cycle-1:implementation:1",
            )
        )


def test_codex_exec_provider_rejects_invalid_configuration_and_schema(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        CodexExecProvider(command=())
    with pytest.raises(ValueError, match="absolute"):
        CodexExecProvider(thread_journal_root=Path("relative"))

    provider = CodexExecProvider(command=(sys.executable, "-c", ""))
    with pytest.raises(CodexProviderError, match="output schemas"):
        provider.run_turn(
            CodexTurnRequest(
                prompt="schema",
                cwd=tmp_path.resolve(),
                sandbox=CodexSandbox.READ_ONLY,
                output_schema={"type": "object"},
            )
        )


def test_codex_exec_provider_rejects_nonzero_and_incomplete_turns(tmp_path: Path) -> None:
    failing = tmp_path / "failing_codex.py"
    failing.write_text("raise SystemExit(7)\n", encoding="utf-8")
    provider = CodexExecProvider(command=(sys.executable, str(failing)))
    request = CodexTurnRequest(
        prompt="fail",
        cwd=tmp_path.resolve(),
        sandbox=CodexSandbox.WORKSPACE_WRITE,
    )
    with pytest.raises(CodexProviderError, match="code 7"):
        provider.run_turn(request)

    incomplete = tmp_path / "incomplete_codex.py"
    incomplete.write_text(
        "import json; print(json.dumps({'type': 'thread.started', 'thread_id': 't'}))\n",
        encoding="utf-8",
    )
    incomplete_provider = CodexExecProvider(command=(sys.executable, str(incomplete)))
    with pytest.raises(CodexProviderError, match="complete"):
        incomplete_provider.run_turn(request)


def test_codex_exec_provider_rejects_unusable_thread_journal(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    provider = CodexExecProvider(
        command=(sys.executable, "-c", ""),
        thread_journal_root=journal,
    )
    resume_key = "cycle-journal"
    journal_path = journal / "exec" / (hashlib.sha256(resume_key.encode()).hexdigest() + ".json")
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text("[]", encoding="utf-8")
    with pytest.raises(CodexProviderError, match="malformed"):
        provider.run_turn(
            CodexTurnRequest(
                prompt="journal",
                cwd=tmp_path.resolve(),
                sandbox=CodexSandbox.READ_ONLY,
                resume_key=resume_key,
            )
        )

    journal_path.write_text('{"thread_id": 7}', encoding="utf-8")
    with pytest.raises(CodexProviderError, match="thread_id is invalid"):
        provider.run_turn(
            CodexTurnRequest(
                prompt="journal",
                cwd=tmp_path.resolve(),
                sandbox=CodexSandbox.READ_ONLY,
                resume_key=resume_key,
            )
        )


def test_codex_exec_provider_rejects_malformed_output_and_timeout(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed_codex.py"
    malformed.write_text(
        "import json\n"
        "print('not-json')\n"
        "print(json.dumps([]))\n"
        "print(json.dumps({}))\n"
        "print(json.dumps({'type': 1}))\n"
        "print(json.dumps({'type': 'item.completed', 'item': {'type': 'other'}}))\n"
        "print(json.dumps({'type': 'turn.completed'}))\n",
        encoding="utf-8",
    )
    provider = CodexExecProvider(command=(sys.executable, str(malformed)))
    request = CodexTurnRequest(
        prompt="malformed",
        cwd=tmp_path.resolve(),
        sandbox=CodexSandbox.READ_ONLY,
    )
    with pytest.raises(CodexProviderError, match="thread identity"):
        provider.run_turn(request)

    slow = tmp_path / "slow_codex.py"
    slow.write_text("import time; time.sleep(3)\n", encoding="utf-8")
    slow_provider = CodexExecProvider(command=(sys.executable, str(slow)))
    with pytest.raises(CodexProviderError, match="timed out"):
        slow_provider.run_turn(
            CodexTurnRequest(
                prompt="timeout",
                cwd=tmp_path.resolve(),
                sandbox=CodexSandbox.READ_ONLY,
                timeout_seconds=1,
            )
        )


def test_codex_exec_provider_rejects_changed_thread_journal(tmp_path: Path) -> None:
    first = tmp_path / "first_thread_codex.py"
    first.write_text(
        "import json\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'old-thread'}))\n"
        "print(json.dumps({'type': 'turn.completed'}))\n",
        encoding="utf-8",
    )
    journal = tmp_path / "journal"
    first_provider = CodexExecProvider(
        command=(sys.executable, str(first)),
        thread_journal_root=journal,
    )
    first_provider.run_turn(
        CodexTurnRequest(
            prompt="initial",
            cwd=tmp_path.resolve(),
            sandbox=CodexSandbox.READ_ONLY,
            resume_key="cycle-changed",
        )
    )

    fake = tmp_path / "different_thread_codex.py"
    fake.write_text(
        "import json\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'new-thread'}))\n"
        "print(json.dumps({'type': 'turn.completed'}))\n",
        encoding="utf-8",
    )
    provider = CodexExecProvider(
        command=(sys.executable, str(fake)),
        thread_journal_root=journal,
    )
    with pytest.raises(CodexProviderError, match="already bound"):
        provider.run_turn(
            CodexTurnRequest(
                prompt="changed",
                cwd=tmp_path.resolve(),
                sandbox=CodexSandbox.READ_ONLY,
                resume_key="cycle-changed",
            )
        )
