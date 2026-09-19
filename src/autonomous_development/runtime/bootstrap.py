from __future__ import annotations

from datetime import datetime
from pathlib import Path

from alembic.config import Config
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine

from alembic import command
from autonomous_development.adapters.docker_cli.deployment import DockerDeploymentProvider
from autonomous_development.adapters.evidence.local import LocalEvidenceStore
from autonomous_development.adapters.git_cli.repository import GitCliRepository
from autonomous_development.adapters.http_observer.deployment import HttpDeploymentObserver
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.target_registry import (
    SqlObjectiveRepository,
    SqlTargetRepository,
)
from autonomous_development.adapters.process.subprocess_runner import SubprocessRunner
from autonomous_development.adapters.target_contract.toml import TomlTargetContractLoader
from autonomous_development.adapters.traffic.file import AtomicFileTrafficDirector
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.enums import DeploymentState
from autonomous_development.domain.models import (
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
    ReleasedVersion,
)
from autonomous_development.ports.deployment import DeploymentSpec
from autonomous_development.ports.traffic import TrafficSplit
from autonomous_development.runtime.config import RuntimeSettings


class BootstrapMutationPolicy(BaseModel):
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...] = ()
    max_changed_files: int = Field(default=50, ge=1)
    max_implementation_attempts: int = Field(default=3, ge=1)


class BootstrapObjective(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=4000)
    acceptance_criteria: tuple[str, ...]
    primary_metrics: tuple[str, ...] = ()
    reliability_constraints: tuple[str, ...] = ()
    performance_constraints: tuple[str, ...] = ()
    security_constraints: tuple[str, ...] = ()
    mutation_policy: BootstrapMutationPolicy
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_created_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("objective created_at must be timezone-aware")
        return value


class BootstrapBaselineRelease(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-fA-F]{64}$")
    deployment_id: str = Field(min_length=1, max_length=80)
    promoted_at: datetime

    @field_validator("promoted_at")
    @classmethod
    def require_promoted_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("baseline promoted_at must be timezone-aware")
        return value


class BootstrapManifest(BaseModel):
    target_id: str = Field(min_length=1, max_length=128)
    repository: Path
    default_branch: str = Field(default="main", min_length=1, max_length=256)
    objective: BootstrapObjective
    baseline_release: BootstrapBaselineRelease

    @field_validator("repository")
    @classmethod
    def require_absolute_repository(cls, value: Path) -> Path:
        resolved = value.expanduser()
        if not resolved.is_absolute():
            raise ValueError("bootstrap repository must be an absolute path")
        return resolved.resolve(strict=True)


def bootstrap_runtime(settings: RuntimeSettings, manifest_path: Path) -> dict[str, object]:
    manifest = BootstrapManifest.model_validate_json(
        manifest_path.resolve(strict=True).read_text(encoding="utf-8")
    )
    if manifest.objective.id == manifest.baseline_release.id:
        raise ValueError("objective and release ids must be distinct")

    _upgrade_database(settings)
    settings.state_root.mkdir(parents=True, exist_ok=True)
    settings.evidence_root.mkdir(parents=True, exist_ok=True)

    runner = SubprocessRunner()
    repository = GitCliRepository()
    baseline = repository.verify_baseline(manifest.repository, manifest.default_branch)
    contract = TomlTargetContractLoader().load(str(manifest.repository))
    if contract.target_id != manifest.target_id:
        raise ValueError("bootstrap target id differs from target contract")
    if contract.revision is None:
        raise RuntimeError("target contract loader did not provide a content revision")

    policy = MutationPolicy(
        allowed_paths=manifest.objective.mutation_policy.allowed_paths,
        forbidden_paths=manifest.objective.mutation_policy.forbidden_paths,
        max_changed_files=manifest.objective.mutation_policy.max_changed_files,
        max_implementation_attempts=(
            manifest.objective.mutation_policy.max_implementation_attempts
        ),
    )
    objective = ProductObjectiveRevision(
        id=manifest.objective.id,
        target_id=manifest.target_id,
        statement=manifest.objective.statement,
        acceptance_criteria=manifest.objective.acceptance_criteria,
        primary_metrics=manifest.objective.primary_metrics,
        reliability_constraints=manifest.objective.reliability_constraints,
        performance_constraints=manifest.objective.performance_constraints,
        security_constraints=manifest.objective.security_constraints,
        mutation_policy=policy,
        created_at=manifest.objective.created_at,
    )
    release = ReleasedVersion(
        id=manifest.baseline_release.id,
        target_id=manifest.target_id,
        source_commit=baseline.commit,
        source_tree=baseline.tree,
        artifact_digest=manifest.baseline_release.artifact_digest.lower(),
        objective_revision_id=objective.id,
        deployment_id=manifest.baseline_release.deployment_id,
        promoted_at=manifest.baseline_release.promoted_at,
    )
    target = DevelopmentTarget(
        id=manifest.target_id,
        repository=str(manifest.repository),
        default_branch=manifest.default_branch,
        target_contract_revision=contract.revision,
        active_objective_revision_id=objective.id,
        mutation_policy=policy,
        current_release_id=release.id,
    )

    evidence_store = LocalEvidenceStore(settings.evidence_root)
    deployment_provider = DockerDeploymentProvider(
        runner,
        evidence_store,
    )
    deployment_spec = DeploymentSpec(
        deployment_id=release.deployment_id,
        target_id=release.target_id,
        artifact_id=f"bootstrap:{release.id}",
        image_digest=release.artifact_digest,
        container_port=contract.deployment.container_port,
    )
    runtime = deployment_provider.ensure(deployment_spec)
    observed = HttpDeploymentObserver(evidence_store).wait_ready(
        deployment_spec,
        runtime,
        health_path=contract.deployment.health_path,
        readiness_path=contract.deployment.readiness_path,
        timeout_seconds=contract.deployment.startup_timeout_seconds,
    )
    if observed.state is not DeploymentState.READY:
        raise RuntimeError("baseline deployment did not become ready")

    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    try:
        targets = TargetRegistryService(
            SqlTargetRepository(engine),
            SqlObjectiveRepository(engine),
        )
        existing = targets.list_targets()
        if existing and (len(existing) != 1 or existing[0].id != target.id):
            raise ValueError("V1 bootstrap refuses a database registered to another target")
        targets.register(target, objective)

        releases = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
        releases.register(release)
        releases.set_serving(
            target.id,
            release.id,
            operation_id=f"bootstrap:{target.id}:{release.id}:serving",
        )
    finally:
        engine.dispose()

    traffic = AtomicFileTrafficDirector(settings.traffic_state_root, evidence_store)
    traffic.apply(
        TrafficSplit(
            experiment_id=f"bootstrap-{release.id}",
            stage_index=0,
            control_base_url=runtime.base_url,
            candidate_base_url=runtime.base_url,
            candidate_weight_percent=0,
            operation_id=f"bootstrap:{target.id}:{release.id}:traffic",
            target_id=target.id,
            control_release_id=release.id,
            candidate_deployment_id=release.deployment_id,
        )
    )

    return {
        "status": "bootstrapped",
        "target_id": target.id,
        "objective_revision_id": objective.id,
        "release_id": release.id,
        "source_commit": release.source_commit,
        "source_tree": release.source_tree,
        "target_contract_revision": contract.revision,
        "deployment_id": release.deployment_id,
        "base_url": runtime.base_url,
    }


def _upgrade_database(settings: RuntimeSettings) -> None:
    config = Config("alembic.ini")
    database_url = settings.database_url.get_secret_value().replace("%", "%%")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
