from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dbos import DBOS, DBOSConfig
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine

from autonomous_development.adapters.canary_proxy.metrics import CanaryMetricsRegistry
from autonomous_development.adapters.canary_proxy.observer import ProxyCanaryObserver
from autonomous_development.adapters.canary_proxy.router import create_canary_proxy
from autonomous_development.adapters.codex_app_server.client import CodexAppServer
from autonomous_development.adapters.docker_cli.build import DockerBuildProvider
from autonomous_development.adapters.docker_cli.deployment import DockerDeploymentProvider
from autonomous_development.adapters.evidence.local import LocalEvidenceStore
from autonomous_development.adapters.git_cli.repository import GitCliRepository
from autonomous_development.adapters.http_observer.deployment import HttpDeploymentObserver
from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.diagnoses import SqlDiagnosisRepository
from autonomous_development.adapters.postgres.evidence_windows import (
    SqlEvidenceWindowRepository,
)
from autonomous_development.adapters.postgres.experiments import SqlExperimentRepository
from autonomous_development.adapters.postgres.feedback import SqlFeedbackRepository
from autonomous_development.adapters.postgres.feedback_triggers import (
    SqlFeedbackTriggerRepository,
)
from autonomous_development.adapters.postgres.proposals import SqlChangeProposalRepository
from autonomous_development.adapters.postgres.request_attributions import (
    SqlRequestAttributionRepository,
)
from autonomous_development.adapters.postgres.release_decisions import (
    SqlReleaseDecisionRepository,
)
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.soak_decisions import (
    SqlSoakDecisionRepository,
)
from autonomous_development.adapters.postgres.target_registry import (
    SqlObjectiveRepository,
    SqlTargetRepository,
)
from autonomous_development.adapters.process.subprocess_runner import SubprocessRunner
from autonomous_development.adapters.prometheus.telemetry import (
    PrometheusTelemetryProvider,
)
from autonomous_development.adapters.quality.command import CommandQualityGate
from autonomous_development.adapters.quality.k6 import K6PerformanceGateFactory
from autonomous_development.adapters.supply_chain.syft_grype import SyftGrypeScanner
from autonomous_development.adapters.target_contract.toml import TomlTargetContractLoader
from autonomous_development.adapters.traffic.file import AtomicFileTrafficDirector
from autonomous_development.api.app import create_control_app
from autonomous_development.application.build import BuildService
from autonomous_development.application.canary import CanaryService
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.deployment import DeploymentService
from autonomous_development.application.diagnosis import DiagnosisService
from autonomous_development.application.engineering import EngineeringService
from autonomous_development.application.evidence_windows import EvidenceWindowService
from autonomous_development.application.experiments import ExperimentService
from autonomous_development.application.feedback import FeedbackService
from autonomous_development.application.iteration import IterationService
from autonomous_development.application.iteration_scheduler import (
    FeedbackIterationPolicy,
    FeedbackIterationSchedulerService,
)
from autonomous_development.application.proposals import ProposalService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.release_finalization import (
    ReleaseFinalizationService,
)
from autonomous_development.application.release_runtime import ReleaseRuntimeService
from autonomous_development.application.releases import ReleaseService
from autonomous_development.application.soak import PostPromotionSoakService
from autonomous_development.application.source_promotion import SourcePromotionService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.application.verification import VerificationService
from autonomous_development.domain.canary import CanaryGuardrails
from autonomous_development.runtime.config import RuntimeSettings
from autonomous_development.runtime.readiness import RuntimeReadinessService
from autonomous_development.workflows.autonomous_iteration import (
    AutonomousIterationWorkflow,
)
from autonomous_development.workflows.feedback_autonomy import (
    FeedbackAutonomyWorkflow,
    bind_scheduled_feedback_workflow,
    scheduled_feedback_tick,
)
from autonomous_development.workflows.post_promotion_soak import (
    PostPromotionSoakWorkflow,
)


class RuntimeConfigurationError(RuntimeError):
    pass


@dataclass(slots=True)
class RuntimeComposition:
    settings: RuntimeSettings
    engine: Engine
    app: FastAPI
    readiness: RuntimeReadinessService
    target_id: str
    schedule_name: str
    _launched: bool = False

    def launch(self) -> None:
        if self._launched:
            return
        DBOS.launch()
        try:
            DBOS.apply_schedules(
                [
                    {
                        "schedule_name": self.schedule_name,
                        "workflow_fn": scheduled_feedback_tick,
                        "schedule": self.settings.feedback_schedule,
                        "context": self.target_id,
                        "automatic_backfill": False,
                        "cron_timezone": self.settings.schedule_timezone,
                    }
                ]
            )
        except Exception:
            DBOS.destroy(workflow_completion_timeout_sec=5)
            raise
        self._launched = True

    def close(self) -> None:
        if self._launched:
            DBOS.destroy(workflow_completion_timeout_sec=30)
            self._launched = False
        self.engine.dispose()


def compose_runtime(settings: RuntimeSettings) -> RuntimeComposition:
    _prepare_state_root(settings)
    _validate_proxy_binding(settings)

    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    evidence_store = LocalEvidenceStore(settings.evidence_root)
    runner = SubprocessRunner()
    repository = GitCliRepository()
    contract_loader = TomlTargetContractLoader()

    cycle_repository = SqlCycleRepository(engine)
    release_repository = SqlReleasedVersionRepository(engine)
    feedback_repository = SqlFeedbackRepository(engine)
    attribution_repository = SqlRequestAttributionRepository(engine)
    target_repository = SqlTargetRepository(engine)
    objective_repository = SqlObjectiveRepository(engine)

    cycles = CycleService(cycle_repository)
    releases = ReleaseCatalogService(release_repository)
    targets = TargetRegistryService(target_repository, objective_repository)
    feedback = FeedbackService(
        releases,
        feedback_repository,
        attribution_repository,
    )

    registered = targets.list_targets()
    if len(registered) != 1:
        engine.dispose()
        raise RuntimeConfigurationError(
            f"V1 requires exactly one registered target, found {len(registered)}"
        )
    target = registered[0]
    repository_root = Path(target.repository).expanduser().resolve(strict=True)
    objective = targets.get_active_objective(target)
    serving = releases.serving(target.id)
    if serving is None:
        engine.dispose()
        raise RuntimeConfigurationError("registered target has no serving release")
    if serving.objective_revision_id != objective.id:
        engine.dispose()
        raise RuntimeConfigurationError(
            "serving release is not governed by the active objective revision"
        )
    contract = contract_loader.load(str(repository_root))
    if contract.target_id != target.id:
        engine.dispose()
        raise RuntimeConfigurationError("target contract identity differs from registry")
    if contract.revision != target.target_contract_revision:
        engine.dispose()
        raise RuntimeConfigurationError("target contract revision differs from registry")

    codex = CodexAppServer(
        thread_journal_root=settings.codex_thread_journal_root,
    )
    diagnosis = DiagnosisService(
        codex,
        feedback_repository,
        SqlDiagnosisRepository(engine),
    )
    proposals = ProposalService(SqlChangeProposalRepository(engine))
    iterations = IterationService(cycles, repository, diagnosis, proposals)

    telemetry = PrometheusTelemetryProvider(
        settings.prometheus_base_url,
        settings.telemetry_queries,
        evidence_store,
    )
    windows = EvidenceWindowService(
        releases,
        feedback_repository,
        telemetry,
        SqlEvidenceWindowRepository(engine),
    )
    scheduler = FeedbackIterationSchedulerService(
        cycles=cycles,
        releases=releases,
        targets=targets,
        feedback=feedback_repository,
        triggers=SqlFeedbackTriggerRepository(engine),
        evidence=windows,
        iterations=iterations,
        contracts=contract_loader,
        policy=FeedbackIterationPolicy(
            minimum_severity=settings.minimum_feedback_severity,
            diagnosis_delay_seconds=settings.diagnosis_delay_seconds,
            evidence_lookback_seconds=settings.evidence_lookback_seconds,
            minimum_diagnosis_confidence=settings.minimum_diagnosis_confidence,
        ),
    )

    verification = VerificationService(
        CommandQualityGate(
            gate_id=gate.id,
            command=gate.command,
            runner=runner,
            evidence=evidence_store,
            timeout_seconds=gate.timeout_seconds,
        )
        for gate in contract.verification.gates
    )
    build = BuildService(
        DockerBuildProvider(runner, evidence_store),
        SyftGrypeScanner(runner, evidence_store),
    )
    deployment_provider = DockerDeploymentProvider(runner, evidence_store)
    deployment = DeploymentService(
        deployment_provider,
        HttpDeploymentObserver(evidence_store),
    )
    release_runtime = ReleaseRuntimeService(
        releases,
        deployment_provider,
        contract,
    )
    experiments = ExperimentService(SqlExperimentRepository(engine))
    traffic = AtomicFileTrafficDirector(settings.traffic_state_root, evidence_store)
    canary_observer = ProxyCanaryObserver(
        settings.canary_proxy_base_url,
        evidence_store,
        observation_timeout_seconds=settings.canary_observation_timeout_seconds,
    )
    canary = CanaryService(traffic, canary_observer, experiments)
    release_controller = ReleaseService(
        cycles,
        experiments,
        SqlReleaseDecisionRepository(engine),
    )
    engineering = EngineeringService(repository, codex)
    finalization = ReleaseFinalizationService(releases)
    source_promotion = SourcePromotionService(repository)
    performance_gates = K6PerformanceGateFactory(
        runner=runner,
        evidence=evidence_store,
    )

    dbos_config: DBOSConfig = {
        "name": "autonomous-development-v1",
        "application_version": "0.1.0",
        "system_database_url": settings.dbos_system_database_url.get_secret_value(),
    }
    DBOS(config=dbos_config)

    execution = AutonomousIterationWorkflow(
        cycles=cycles,
        proposals=proposals,
        engineering=engineering,
        verification=verification,
        build=build,
        deployment=deployment,
        performance_gates=performance_gates,
        experiments=experiments,
        canary=canary,
        releases=release_controller,
        finalization=finalization,
        release_runtime=release_runtime,
        source_promotion=source_promotion,
        contract=contract,
        repository_root=repository_root,
        worktree_root=settings.worktree_root,
        default_branch=target.default_branch,
        canary_hold_sleep_seconds=settings.canary_hold_sleep_seconds,
        config_name=f"autonomous-iteration-{_safe_name(target.id)}",
    )
    soak_service = PostPromotionSoakService(
        cycles,
        traffic,
        canary_observer,
        releases,
        SqlSoakDecisionRepository(engine),
        release_runtime,
        source_promotion,
        repository_root=repository_root,
        default_branch=target.default_branch,
    )
    guardrails = CanaryGuardrails(
        max_candidate_error_rate=contract.canary.max_candidate_error_rate,
        max_error_rate_delta=contract.canary.max_error_rate_delta,
        max_candidate_p95_latency_ms=contract.canary.max_candidate_p95_latency_ms,
        max_p95_latency_ratio=contract.canary.max_p95_latency_ratio,
    )
    soak = PostPromotionSoakWorkflow(
        soak_service,
        stage=contract.canary.stages[-1],
        guardrails=guardrails,
        hold_sleep_seconds=settings.soak_hold_sleep_seconds,
        config_name=f"post-promotion-soak-{_safe_name(target.id)}",
    )
    autonomy = FeedbackAutonomyWorkflow(
        scheduler,
        execution,
        soak,
        config_name=f"feedback-autonomy-{_safe_name(target.id)}",
    )
    bind_scheduled_feedback_workflow(autonomy)

    readiness = RuntimeReadinessService(
        settings=settings,
        engine=engine,
        targets=targets,
        releases=releases,
        contracts=contract_loader,
        runner=runner,
    )
    metrics = CanaryMetricsRegistry()
    proxy_app = create_canary_proxy(
        traffic,
        metrics,
        attributions=attribution_repository,
    )
    app = create_control_app(
        feedback,
        readiness,
        product_app=proxy_app,
        product_mount_path="/product",
    )

    return RuntimeComposition(
        settings=settings,
        engine=engine,
        app=app,
        readiness=readiness,
        target_id=target.id,
        schedule_name=f"autodev-feedback-{_safe_name(target.id)}",
    )


def _prepare_state_root(settings: RuntimeSettings) -> None:
    settings.state_root.mkdir(parents=True, exist_ok=True)
    settings.evidence_root.mkdir(parents=True, exist_ok=True)
    settings.traffic_state_root.mkdir(parents=True, exist_ok=True)
    settings.worktree_root.mkdir(parents=True, exist_ok=True)
    settings.codex_thread_journal_root.mkdir(parents=True, exist_ok=True)


def _validate_proxy_binding(settings: RuntimeSettings) -> None:
    parsed = urlsplit(settings.canary_proxy_base_url)
    if parsed.hostname != "127.0.0.1":
        raise RuntimeConfigurationError("canary proxy URL must use 127.0.0.1")
    if parsed.port != settings.api_port or parsed.path.rstrip("/") != "/product":
        raise RuntimeConfigurationError(
            "canary proxy URL must be the control API /product mount on api_port"
        )


def _safe_name(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    prefix = "".join(character if character.isalnum() else "-" for character in value)
    prefix = prefix.strip("-")[:48] or "target"
    return f"{prefix}-{digest}"
