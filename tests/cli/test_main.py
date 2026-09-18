from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from autonomous_development.cli import main as cli
from autonomous_development.ports.readiness import ReadinessCheck, ReadinessReport


class FakeReadiness:
    def __init__(self, ready: bool) -> None:
        self.ready = ready
        self.calls = 0

    def check(self) -> ReadinessReport:
        self.calls += 1
        return ReadinessReport(
            ready=self.ready,
            checks=(ReadinessCheck("database", self.ready, "ok"),),
        )


class FakeRuntime:
    def __init__(self, *, ready: bool = True) -> None:
        self.readiness = FakeReadiness(ready)
        self.app = object()
        self.launched = 0
        self.closed = 0

    def launch(self) -> None:
        self.launched += 1

    def close(self) -> None:
        self.closed += 1


def test_ready_command_prints_json_and_closes(monkeypatch, capsys) -> None:
    runtime = FakeRuntime(ready=True)
    settings = SimpleNamespace(api_host="127.0.0.1", api_port=8765)
    monkeypatch.setattr(cli.RuntimeSettings, "from_environment", lambda: settings)
    monkeypatch.setattr(cli, "compose_runtime", lambda configured: runtime)
    monkeypatch.setattr(sys, "argv", ["autonomous-development", "ready"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    assert payload["checks"][0]["name"] == "database"
    assert runtime.launched == 0
    assert runtime.closed == 1


def test_serve_command_launches_uvicorn_and_closes(monkeypatch) -> None:
    runtime = FakeRuntime()
    settings = SimpleNamespace(api_host="127.0.0.1", api_port=9876)
    calls: list[tuple[object, str, int, str]] = []

    monkeypatch.setattr(cli.RuntimeSettings, "from_environment", lambda: settings)
    monkeypatch.setattr(cli, "compose_runtime", lambda configured: runtime)
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, *, host, port, log_level: calls.append(
            (app, host, port, log_level)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["autonomous-development", "serve"])

    cli.main()

    assert runtime.launched == 1
    assert runtime.closed == 1
    assert calls == [(runtime.app, "127.0.0.1", 9876, "info")]
