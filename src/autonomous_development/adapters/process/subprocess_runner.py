from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from autonomous_development.ports.process import (
    CommandRequest,
    CommandResult,
    CommandTimedOut,
    CommandUnavailable,
)

_DEFAULT_ENVIRONMENT_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "PATHEXT",
)


class SubprocessRunner:
    def __init__(self, *, max_output_chars: int = 131_072) -> None:
        if max_output_chars < 1024:
            raise ValueError("max_output_chars must be at least 1024")
        self._max_output_chars = max_output_chars

    def run(self, request: CommandRequest) -> CommandResult:
        command = _resolve_command(request.command)
        environment = {
            key: value
            for key in _DEFAULT_ENVIRONMENT_KEYS
            if (value := os.environ.get(key)) is not None
        }
        environment.update(request.environment)
        environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        if _is_pytest_command(command):
            environment["PYTEST_ADDOPTS"] = _append_pytest_option(
                environment.get("PYTEST_ADDOPTS", ""),
                "-p no:cacheprovider",
            )
        if "PATH" not in request.environment:
            python_directory = _runtime_python_directory()
            inherited_path = environment.get("PATH", "")
            environment["PATH"] = os.pathsep.join(
                part for part in (python_directory, inherited_path) if part
            )
        try:
            completed = subprocess.run(
                command,
                cwd=request.cwd,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=request.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CommandUnavailable(request.command[0]) from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandTimedOut(
                f"command exceeded {request.timeout_seconds}s: {request.command[0]}"
            ) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=_bounded(completed.stdout, self._max_output_chars),
            stderr=_bounded(completed.stderr, self._max_output_chars),
        )


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = "\n...[truncated by autonomous-development]...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return value[:head] + marker + value[-tail:]


def _runtime_python_directory() -> str:
    package_root = Path(__file__).resolve().parents[4]
    executable_name = "python.exe" if os.name == "nt" else "python"
    virtual_environment = package_root / ".venv" / (
        "Scripts" if os.name == "nt" else "bin"
    )
    project_python = virtual_environment / executable_name
    if project_python.exists():
        return str(virtual_environment)
    return os.path.dirname(sys.executable)


def _resolve_command(command: tuple[str, ...]) -> list[str]:
    resolved = list(command)
    if resolved and resolved[0].lower() in {"python", "python.exe"}:
        executable_name = "python.exe" if os.name == "nt" else "python"
        project_python = Path(_runtime_python_directory()) / executable_name
        if project_python.exists():
            resolved[0] = str(project_python)
    return resolved


def _is_pytest_command(command: list[str]) -> bool:
    return any(Path(part).name.lower() == "pytest" for part in command)


def _append_pytest_option(existing: str, option: str) -> str:
    parts = existing.split()
    if option in parts:
        return existing
    return " ".join((*parts, option))
