from __future__ import annotations

import hashlib
import re
from pathlib import Path

from autonomous_development.ports.deployment import (
    DeploymentProvider,
    DeploymentProviderError,
    DeploymentRuntime,
    DeploymentSpec,
)
from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.process import CommandRequest, ProcessRunner

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


class DockerDeploymentProvider(DeploymentProvider):
    def __init__(
        self,
        runner: ProcessRunner,
        evidence: EvidenceStore,
        *,
        timeout_seconds: int = 120,
    ) -> None:
        self._runner = runner
        self._evidence = evidence
        self._timeout_seconds = timeout_seconds

    def ensure(self, spec: DeploymentSpec) -> DeploymentRuntime:
        name = _container_name(spec.deployment_id)
        existing = self._inspect(spec, name)
        if existing is not None:
            return existing

        result = self._runner.run(
            CommandRequest(
                command=(
                    "docker",
                    "run",
                    "--detach",
                    "--pull",
                    "never",
                    "--restart",
                    "no",
                    "--name",
                    name,
                    "--label",
                    f"autodev.deployment={spec.deployment_id}",
                    "--label",
                    f"autodev.target={spec.target_id}",
                    "--publish",
                    f"127.0.0.1::{spec.container_port}",
                    spec.image_digest,
                ),
                cwd=_cwd(),
                timeout_seconds=self._timeout_seconds,
            )
        )
        evidence_ref = self._evidence.write_json(
            "deployment-effect",
            spec.deployment_id,
            {
                "deployment_id": spec.deployment_id,
                "target_id": spec.target_id,
                "artifact_id": spec.artifact_id,
                "image_digest": spec.image_digest,
                "returncode": result.returncode,
                "stdout_sha256": _digest(result.stdout),
                "stderr_sha256": _digest(result.stderr),
            },
        )

        # A non-zero return or lost/ambiguous acknowledgement is never retried
        # blindly. Re-read Docker reality using the deterministic container name.
        reconciled = self._inspect(spec, name, effect_evidence_ref=evidence_ref)
        if reconciled is not None:
            return reconciled
        raise DeploymentProviderError(
            f"container creation was not reconciled for {spec.deployment_id}: {evidence_ref}"
        )

    def stop(self, deployment_id: str) -> None:
        name = _container_name(deployment_id)
        result = self._runner.run(
            CommandRequest(
                command=("docker", "rm", "--force", name),
                cwd=_cwd(),
                timeout_seconds=self._timeout_seconds,
            )
        )
        if result.returncode != 0 and "No such container" not in result.stderr:
            raise DeploymentProviderError(f"failed to stop deployment {deployment_id}")

    def _inspect(
        self,
        spec: DeploymentSpec,
        name: str,
        *,
        effect_evidence_ref: str | None = None,
    ) -> DeploymentRuntime | None:
        inspected = self._runner.run(
            CommandRequest(
                command=(
                    "docker",
                    "inspect",
                    "--format",
                    '{{.Id}}|{{.Image}}|{{index .Config.Labels "autodev.deployment"}}|'
                    '{{index .Config.Labels "autodev.target"}}',
                    name,
                ),
                cwd=_cwd(),
                timeout_seconds=self._timeout_seconds,
            )
        )
        if inspected.returncode != 0:
            if "No such object" in inspected.stderr or "No such container" in inspected.stderr:
                return None
            raise DeploymentProviderError("Docker inspect failed while reconciling deployment")

        fields = inspected.stdout.strip().split("|")
        if len(fields) != 4:
            raise DeploymentProviderError("Docker inspect returned an unexpected identity shape")
        container_id, image_digest, deployment_id, target_id = fields
        if (
            image_digest != spec.image_digest
            or deployment_id != spec.deployment_id
            or target_id != spec.target_id
        ):
            raise DeploymentProviderError(
                "existing deployment identity conflicts with requested artifact or target"
            )

        port = self._runner.run(
            CommandRequest(
                command=("docker", "port", name, f"{spec.container_port}/tcp"),
                cwd=_cwd(),
                timeout_seconds=self._timeout_seconds,
            )
        )
        if port.returncode != 0:
            raise DeploymentProviderError("Docker port read-back failed")
        bindings = tuple(line.strip() for line in port.stdout.splitlines() if line.strip())
        if len(bindings) != 1 or not bindings[0].startswith("127.0.0.1:"):
            raise DeploymentProviderError("deployment is not bound to exactly one loopback port")
        host_port = bindings[0].rsplit(":", 1)[-1]
        if not host_port.isdigit():
            raise DeploymentProviderError("Docker returned an invalid host port")

        ref = effect_evidence_ref or self._evidence.write_json(
            "deployment-reconcile",
            spec.deployment_id,
            {
                "deployment_id": spec.deployment_id,
                "container_id": container_id,
                "image_digest": image_digest,
                "loopback_port": int(host_port),
            },
        )
        return DeploymentRuntime(
            deployment_id=spec.deployment_id,
            container_id=container_id,
            base_url=f"http://127.0.0.1:{host_port}",
            evidence_ref=ref,
        )


def _container_name(deployment_id: str) -> str:
    if not _SAFE_ID.fullmatch(deployment_id):
        raise ValueError("deployment_id contains characters unsafe for Docker identity")
    return f"autodev-{deployment_id}"


def _cwd() -> Path:
    return Path.cwd().resolve()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
