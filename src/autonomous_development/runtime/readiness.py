from __future__ import annotations

import shutil
import sys
from pathlib import Path

import httpx
from sqlalchemy import Engine, text

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.ports.process import CommandRequest, ProcessRunner
from autonomous_development.ports.readiness import ReadinessCheck, ReadinessReport
from autonomous_development.ports.target_contract import TargetContractLoader
from autonomous_development.ports.traffic import TrafficRouteReader

from .config import RuntimeSettings


class RuntimeReadinessService:
    def __init__(
        self,
        *,
        settings: RuntimeSettings,
        engine: Engine,
        targets: TargetRegistryService,
        releases: ReleaseCatalogService,
        contracts: TargetContractLoader,
        runner: ProcessRunner,
        traffic: TrafficRouteReader | None = None,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._targets = targets
        self._releases = releases
        self._contracts = contracts
        self._runner = runner
        self._traffic = traffic
        self._http_transport = http_transport

    def check(self) -> ReadinessReport:
        checks = [
            self._database(),
            self._python(),
            self._command("git", ("git", "--version")),
            self._command("uv", ("uv", "--version")),
            self._command("codex", ("codex", "--version")),
            self._command(
                "docker-daemon",
                ("docker", "version", "--format", "{{.Server.Version}}"),
            ),
            self._command("k6", ("k6", "version")),
            self._command("syft", ("syft", "version")),
            self._command("grype", ("grype", "version")),
            self._runtime_state(),
            self._prometheus(),
            self._canary_proxy(),
        ]
        return ReadinessReport(
            ready=all(check.ready for check in checks),
            checks=tuple(checks),
        )

    def _database(self) -> ReadinessCheck:
        try:
            with self._engine.connect() as connection:
                value = connection.execute(text("SELECT 1")).scalar_one()
            if value != 1:
                return ReadinessCheck("database", False, "SELECT 1 returned unexpected value")
            return ReadinessCheck("database", True, "reachable")
        except Exception as exc:
            return ReadinessCheck("database", False, type(exc).__name__)

    def _python(self) -> ReadinessCheck:
        version = sys.version_info
        ready = version >= (3, 12) and version < (3, 15)
        return ReadinessCheck(
            "python",
            ready,
            f"{version.major}.{version.minor}.{version.micro}",
        )

    def _command(self, name: str, command: tuple[str, ...]) -> ReadinessCheck:
        binary = command[0]
        if shutil.which(binary) is None:
            return ReadinessCheck(name, False, "command not found")
        try:
            result = self._runner.run(
                CommandRequest(
                    command=command,
                    cwd=self._settings.state_root,
                    timeout_seconds=15,
                )
            )
        except Exception as exc:
            return ReadinessCheck(name, False, type(exc).__name__)
        if result.returncode != 0:
            return ReadinessCheck(name, False, f"exit {result.returncode}")
        return ReadinessCheck(name, True, "functional")

    def _runtime_state(self) -> ReadinessCheck:
        try:
            targets = self._targets.list_targets()
            if len(targets) != 1:
                return ReadinessCheck(
                    "registered-target",
                    False,
                    f"V1 requires exactly one target, found {len(targets)}",
                )
            target = targets[0]
            self._targets.get_active_objective(target)
            serving = self._releases.serving(target.id)
            if serving is None:
                return ReadinessCheck(
                    "registered-target",
                    False,
                    "registered target has no serving release",
                )
            root = Path(target.repository)
            if not root.is_absolute() or not root.is_dir():
                return ReadinessCheck(
                    "registered-target",
                    False,
                    "registered repository is not an existing absolute directory",
                )
            contract = self._contracts.load(str(root))
            if contract.target_id != target.id:
                return ReadinessCheck(
                    "registered-target",
                    False,
                    "target contract identity differs from registry",
                )
            if contract.revision != target.target_contract_revision:
                return ReadinessCheck(
                    "registered-target",
                    False,
                    "target contract revision differs from registry",
                )
            missing = sorted(
                {
                    gate.command[0]
                    for gate in contract.verification.gates
                    if shutil.which(gate.command[0]) is None
                }
            )
            if missing:
                return ReadinessCheck(
                    "registered-target",
                    False,
                    "target gate commands missing: " + ", ".join(missing),
                )
            return ReadinessCheck(
                "registered-target",
                True,
                f"{target.id} serving {serving.id}",
            )
        except Exception as exc:
            return ReadinessCheck("registered-target", False, str(exc))

    def _prometheus(self) -> ReadinessCheck:
        if not self._settings.telemetry_queries:
            return ReadinessCheck(
                "prometheus",
                False,
                "no telemetry queries configured",
            )
        return self._http(
            "prometheus",
            self._settings.prometheus_base_url + "/-/ready",
            accepted={200},
        )

    def _canary_proxy(self) -> ReadinessCheck:
        if self._traffic is not None:
            try:
                route = self._traffic.read_current()
                if route is None:
                    return ReadinessCheck(
                        "canary-proxy",
                        False,
                        "no active product route",
                    )
                targets = self._targets.list_targets()
                if len(targets) != 1 or route.target_id != targets[0].id:
                    return ReadinessCheck(
                        "canary-proxy",
                        False,
                        "active product route is not bound to the registered target",
                    )
                if route.control_release_id is None or route.candidate_deployment_id is None:
                    return ReadinessCheck(
                        "canary-proxy",
                        False,
                        "active product route lacks attribution identity",
                    )
            except Exception as exc:
                return ReadinessCheck("canary-proxy", False, type(exc).__name__)
        return self._http(
            "canary-proxy",
            self._settings.canary_proxy_base_url + "/__autodev/metrics/0",
            accepted={200, 404},
        )

    def _http(
        self,
        name: str,
        url: str,
        *,
        accepted: set[int],
    ) -> ReadinessCheck:
        try:
            with httpx.Client(
                timeout=3.0,
                trust_env=False,
                follow_redirects=False,
                transport=self._http_transport,
            ) as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            return ReadinessCheck(name, False, type(exc).__name__)
        if response.status_code not in accepted:
            return ReadinessCheck(name, False, f"http {response.status_code}")
        return ReadinessCheck(name, True, f"http {response.status_code}")
