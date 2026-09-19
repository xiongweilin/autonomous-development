from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from dbos import DBOS, DBOSConfiguredInstance

from autonomous_development.application.build import BuildService
from autonomous_development.application.canary import CanaryService
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.deployment import DeploymentService
from autonomous_development.application.engineering import EngineeringService
from autonomous_development.application.experiments import ExperimentService
from autonomous_development.application.proposals import ProposalService
from autonomous_development.application.release_finalization import ReleaseFinalizationService
from autonomous_development.application.release_runtime import ReleaseRuntimeService
from autonomous_development.application.releases import ReleaseService
from autonomous_development.application.source_promotion import SourcePromotionService
from autonomous_development.application.verification import VerificationService
from autonomous_development.domain.canary import CanaryGuardrails, CanaryStageDecision
from autonomous_development.domain.enums import (
    CanaryDecisionKind,
    CycleState,
    DeploymentState,
    ReleaseDecisionKind,
    VerificationStatus,
)
from autonomous_development.domain.models import (
    BuildArtifact,
    CandidateRevision,
    ChangeProposal,
    Deployment,
    DevelopmentCycle,
    Experiment,
    VerificationCheck,
    VerificationRun,
)
from autonomous_development.ports.codex import CodexProviderError
from autonomous_development.ports.deployment import DeploymentRuntime
from autonomous_development.ports.quality import PerformanceGateFactory
from autonomous_development.ports.target_contract import TargetContract


@DBOS.dbos_class()
class AutonomousIterationWorkflow(DBOSConfiguredInstance):
    """Durably execute one prepared autonomous change through promotion."""

    def __init__(
        self,
        *,
        cycles: CycleService,
        proposals: ProposalService,
        engineering: EngineeringService,
        verification: VerificationService,
        build: BuildService,
        deployment: DeploymentService,
        performance_gates: PerformanceGateFactory,
        experiments: ExperimentService,
        canary: CanaryService,
        releases: ReleaseService,
        finalization: ReleaseFinalizationService,
        release_runtime: ReleaseRuntimeService,
        source_promotion: SourcePromotionService,
        contract: TargetContract,
        repository_root: Path,
        worktree_root: Path,
        default_branch: str,
        performance_gate_id: str = "performance",
        canary_hold_sleep_seconds: float = 30.0,
        config_name: str = "autonomous-iteration-v1",
    ) -> None:
        if not repository_root.is_absolute() or not worktree_root.is_absolute():
            raise ValueError("iteration workflow paths must be absolute")
        if not default_branch.strip():
            raise ValueError("default branch must be non-empty")
        if not performance_gate_id.strip():
            raise ValueError("performance gate id must be non-empty")
        if canary_hold_sleep_seconds <= 0:
            raise ValueError("canary hold sleep must be positive")
        self._cycles = cycles
        self._proposals = proposals
        self._engineering = engineering
        self._verification = verification
        self._build = build
        self._deployment = deployment
        self._performance_gates = performance_gates
        self._experiments = experiments
        self._canary = canary
        self._releases = releases
        self._finalization = finalization
        self._release_runtime = release_runtime
        self._source_promotion = source_promotion
        self._contract = contract
        self._repository_root = repository_root
        self._worktree_root = worktree_root
        self._default_branch = default_branch
        self._performance_gate_id = performance_gate_id
        self._canary_hold_sleep_seconds = canary_hold_sleep_seconds
        super().__init__(config_name=config_name)

    @DBOS.workflow(max_recovery_attempts=20)
    def run(
        self,
        cycle_id: str,
        proposal_id: str,
        operation_id: str,
        model: str | None = None,
    ) -> dict[str, object]:
        if not cycle_id.strip() or not proposal_id.strip() or not operation_id.strip():
            raise ValueError("cycle, proposal and operation ids must be non-empty")

        proposal_doc = self._load_proposal_step(proposal_id)
        proposal = _proposal_from_document(proposal_doc)
        if proposal.target_id != self._contract.target_id:
            raise ValueError("proposal target does not match configured target contract")
        if self._performance_gate_id not in proposal.mandatory_gates:
            raise ValueError("mandatory gates must include the configured performance gate")

        pre_gates = tuple(
            gate for gate in proposal.mandatory_gates if gate != self._performance_gate_id
        )
        if not pre_gates:
            raise ValueError("at least one pre-deployment verification gate is required")

        self._start_development_step(cycle_id, proposal_id, operation_id)
        try:
            candidate_doc = self._implement_step(
                cycle_id,
                proposal_id,
                operation_id,
                model,
            )
        except Exception as exc:
            return self._terminal_failure_result(
                cycle_id,
                operation_id,
                phase="implementation",
                error=exc,
            )
        self._candidate_ready_step(cycle_id, candidate_doc, operation_id)
        self._start_verification_step(cycle_id, operation_id)

        try:
            pre_verification_doc = self._pre_verification_step(
                candidate_doc,
                pre_gates,
                cycle_id,
            )
        except Exception as exc:
            return self._terminal_failure_result(
                cycle_id,
                operation_id,
                phase="pre-verification",
                error=exc,
            )
        pre_verification = _verification_from_document(pre_verification_doc)
        if not pre_verification.passed:
            cycle_doc = self._reject_step(
                cycle_id,
                operation_id=f"{operation_id}:pre-verification-reject",
            )
            self._best_effort_cleanup(cycle_id, deployment_id=None)
            return _terminal_result(cycle_doc, "pre-deployment verification failed")

        self._verified_step(cycle_id, pre_verification.id, operation_id)
        try:
            artifact_doc = self._build_step(candidate_doc, cycle_id)
        except Exception as exc:
            return self._terminal_failure_result(
                cycle_id,
                operation_id,
                phase="build",
                error=exc,
            )
        self._built_step(cycle_id, artifact_doc, operation_id)
        try:
            deployment_bundle = self._deploy_step(artifact_doc, cycle_id)
        except Exception as exc:
            return self._terminal_failure_result(
                cycle_id,
                operation_id,
                phase="deployment",
                error=exc,
            )
        self._staged_step(cycle_id, deployment_bundle, operation_id)

        try:
            full_verification_doc = self._performance_step(
                candidate_doc,
                pre_verification_doc,
                deployment_bundle,
                proposal_doc,
                cycle_id,
            )
        except Exception as exc:
            return self._terminal_failure_result(
                cycle_id,
                operation_id,
                phase="performance",
                error=exc,
            )
        full_verification = _verification_from_document(full_verification_doc)
        if not full_verification.passed:
            cycle_doc = self._reject_step(
                cycle_id,
                operation_id=f"{operation_id}:performance-reject",
            )
            self._best_effort_cleanup(
                cycle_id,
                deployment_id=f"{cycle_id}-candidate",
            )
            return _terminal_result(cycle_doc, "post-deployment performance gate failed")

        experiment_id = f"{cycle_id}-experiment"
        self._ensure_experiment_step(
            experiment_id,
            proposal.baseline_release_id,
            deployment_bundle,
        )
        self._canarying_step(
            cycle_id,
            experiment_id,
            full_verification.id,
            operation_id,
        )

        canary_round = 0
        while True:
            try:
                canary_doc = self._canary_step(
                    experiment_id,
                    proposal.baseline_release_id,
                    deployment_bundle,
                    operation_id=f"{operation_id}:canary:{canary_round}",
                )
            except Exception as exc:
                self._restore_canary_control_step(
                    experiment_id,
                    operation_id=f"{operation_id}:canary:{canary_round}:failure-restore",
                )
                return self._terminal_failure_result(
                    cycle_id,
                    operation_id,
                    phase="canary",
                    error=exc,
                )
            decision = _canary_decision_from_document(canary_doc)
            cycle_doc = self._apply_canary_step(
                cycle_id,
                canary_doc,
                operation_id=f"{operation_id}:canary:{canary_round}:apply",
            )
            if decision.kind is CanaryDecisionKind.HOLD:
                canary_round += 1
                DBOS.sleep(self._canary_hold_sleep_seconds)
                continue
            if decision.kind is CanaryDecisionKind.ADVANCE:
                canary_round += 1
                continue
            if decision.kind is CanaryDecisionKind.ROLLBACK:
                self._best_effort_cleanup(
                    cycle_id,
                    deployment_id=f"{cycle_id}-candidate",
                )
                return _terminal_result(cycle_doc, "canary regression rolled back")
            if decision.kind is CanaryDecisionKind.PROMOTION_READY:
                break
            raise RuntimeError(f"unsupported canary decision: {decision.kind.value}")

        promotion_doc = self._promote_step(
            cycle_id,
            full_verification_doc,
            proposal_doc,
            operation_id,
        )
        promotion_cycle = _cycle_state_from_document(promotion_doc)
        if promotion_cycle is not CycleState.PROMOTED:
            return _terminal_result(
                promotion_doc,
                "promotion controller did not produce a promoted cycle",
            )

        self._promote_source_step(candidate_doc)
        promoted_at = self._promotion_time_step()
        release_doc = self._finalize_release_step(
            cycle_id,
            candidate_doc,
            artifact_doc,
            deployment_bundle,
            promoted_at,
            operation_id,
        )
        return {
            "cycle": promotion_doc,
            "release": release_doc,
            "verification": full_verification_doc,
            "status": "promoted",
        }

    @DBOS.step(retries_allowed=False)
    def _load_proposal_step(self, proposal_id: str) -> dict[str, object]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError(f"unknown change proposal: {proposal_id}")
        return _proposal_to_document(proposal)

    @DBOS.step(retries_allowed=False)
    def _start_development_step(
        self,
        cycle_id: str,
        proposal_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        if cycle.change_proposal_id != proposal_id:
            raise ValueError("cycle is not bound to the requested proposal")
        if cycle.state is CycleState.DEVELOPING:
            return _cycle_to_document(cycle)
        if cycle.state is not CycleState.CHANGE_PROPOSED:
            raise ValueError("autonomous execution requires a change-proposed cycle")
        updated = self._cycles.transition(
            cycle.id,
            CycleState.DEVELOPING,
            expected_version=cycle.version,
            operation_id=f"{operation_id}:developing",
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _implement_step(
        self,
        cycle_id: str,
        proposal_id: str,
        operation_id: str,
        model: str | None,
    ) -> dict[str, object]:
        del operation_id
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError(f"unknown change proposal: {proposal_id}")

        last_error: CodexProviderError | None = None
        for attempt_number in range(1, proposal.max_implementation_attempts + 1):
            try:
                attempt = self._engineering.implement(
                    proposal,
                    repository_root=self._repository_root,
                    default_branch=self._default_branch,
                    worktree_root=self._worktree_root,
                    cycle_id=cycle_id,
                    attempt=attempt_number,
                    model=model,
                )
                return _candidate_to_document(attempt.candidate)
            except CodexProviderError as exc:
                last_error = exc
        if last_error is None:
            raise RuntimeError("implementation attempt budget produced no attempt")
        raise last_error

    @DBOS.step(retries_allowed=False)
    def _candidate_ready_step(
        self,
        cycle_id: str,
        candidate_doc: dict[str, object],
        operation_id: str,
    ) -> dict[str, object]:
        candidate = _candidate_from_document(candidate_doc)
        cycle = self._cycles.get(cycle_id)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.CANDIDATE_READY,
            expected_version=cycle.version,
            operation_id=f"{operation_id}:candidate-ready",
            candidate_id=candidate.id,
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _start_verification_step(
        self,
        cycle_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.VERIFYING,
            expected_version=cycle.version,
            operation_id=f"{operation_id}:verifying",
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _pre_verification_step(
        self,
        candidate_doc: dict[str, object],
        required_gates: tuple[str, ...],
        cycle_id: str,
    ) -> dict[str, object]:
        run = self._verification.run(
            _candidate_from_document(candidate_doc),
            run_id=f"{cycle_id}-verify-pre",
            required_gates=required_gates,
        )
        return _verification_to_document(run)

    @DBOS.step(retries_allowed=False)
    def _verified_step(
        self,
        cycle_id: str,
        verification_run_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.VERIFIED,
            expected_version=cycle.version,
            operation_id=f"{operation_id}:verified",
            verification_run_id=verification_run_id,
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _build_step(
        self,
        candidate_doc: dict[str, object],
        cycle_id: str,
    ) -> dict[str, object]:
        candidate = _candidate_from_document(candidate_doc)
        root = Path(candidate.worktree_path).resolve()
        artifact = self._build.build(
            candidate,
            artifact_id=f"{cycle_id}-artifact",
            dockerfile=root / self._contract.build.dockerfile,
            dependency_locks=tuple(
                root / path for path in self._contract.build.dependency_locks
            ),
        )
        return _artifact_to_document(artifact)

    @DBOS.step(retries_allowed=False)
    def _built_step(
        self,
        cycle_id: str,
        artifact_doc: dict[str, object],
        operation_id: str,
    ) -> dict[str, object]:
        artifact = _artifact_from_document(artifact_doc)
        cycle = self._cycles.get(cycle_id)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.BUILT,
            expected_version=cycle.version,
            operation_id=f"{operation_id}:built",
            artifact_id=artifact.id,
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _deploy_step(
        self,
        artifact_doc: dict[str, object],
        cycle_id: str,
    ) -> dict[str, object]:
        runtime, deployment = self._deployment.deploy_candidate(
            _artifact_from_document(artifact_doc),
            self._contract,
            deployment_id=f"{cycle_id}-candidate",
        )
        return {
            "runtime": _runtime_to_document(runtime),
            "deployment": _deployment_to_document(deployment),
        }

    @DBOS.step(retries_allowed=False)
    def _staged_step(
        self,
        cycle_id: str,
        deployment_bundle: dict[str, object],
        operation_id: str,
    ) -> dict[str, object]:
        deployment = _deployment_from_document(
            _mapping(deployment_bundle.get("deployment"), "deployment")
        )
        cycle = self._cycles.get(cycle_id)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.STAGED,
            expected_version=cycle.version,
            operation_id=f"{operation_id}:staged",
            candidate_deployment_id=deployment.id,
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _performance_step(
        self,
        candidate_doc: dict[str, object],
        pre_verification_doc: dict[str, object],
        deployment_bundle: dict[str, object],
        proposal_doc: dict[str, object],
        cycle_id: str,
    ) -> dict[str, object]:
        candidate = _candidate_from_document(candidate_doc)
        pre = _verification_from_document(pre_verification_doc)
        proposal = _proposal_from_document(proposal_doc)
        runtime = _runtime_from_document(
            _mapping(deployment_bundle.get("runtime"), "runtime")
        )
        gate = self._performance_gates.create(
            base_url=runtime.base_url,
            script_path=self._contract.performance.script_path,
            required_threshold_metrics=(
                self._contract.performance.required_threshold_metrics
            ),
            timeout_seconds=self._contract.performance.timeout_seconds,
        )
        if gate.gate_id != self._performance_gate_id:
            raise ValueError("performance gate factory returned an unexpected gate id")
        if self._performance_gate_id not in proposal.mandatory_gates:
            raise ValueError("proposal does not authorize the configured performance gate")
        performance_check = gate.evaluate(candidate)
        full = VerificationRun(
            id=f"{cycle_id}-verify-full",
            candidate_id=candidate.id,
            checks=(*pre.checks, performance_check),
        )
        return _verification_to_document(full)

    @DBOS.step(retries_allowed=False)
    def _ensure_experiment_step(
        self,
        experiment_id: str,
        control_release_id: str,
        deployment_bundle: dict[str, object],
    ) -> dict[str, object]:
        deployment = _deployment_from_document(
            _mapping(deployment_bundle.get("deployment"), "deployment")
        )
        experiment = self._experiments.create(
            Experiment(
                id=experiment_id,
                target_id=self._contract.target_id,
                control_release_id=control_release_id,
                candidate_deployment_id=deployment.id,
                stages=self._contract.canary.stages,
            )
        )
        return {
            "id": experiment.id,
            "current_stage_index": experiment.current_stage_index,
        }

    @DBOS.step(retries_allowed=False)
    def _canarying_step(
        self,
        cycle_id: str,
        experiment_id: str,
        verification_run_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.CANARYING,
            expected_version=cycle.version,
            operation_id=f"{operation_id}:canarying",
            verification_run_id=verification_run_id,
            experiment_id=experiment_id,
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _canary_step(
        self,
        experiment_id: str,
        baseline_release_id: str,
        deployment_bundle: dict[str, object],
        operation_id: str,
    ) -> dict[str, object]:
        runtime = _runtime_from_document(
            _mapping(deployment_bundle.get("runtime"), "runtime")
        )
        control_runtime = self._release_runtime.resolve(baseline_release_id)
        guardrails = CanaryGuardrails(
            max_candidate_error_rate=self._contract.canary.max_candidate_error_rate,
            max_error_rate_delta=self._contract.canary.max_error_rate_delta,
            max_candidate_p95_latency_ms=(
                self._contract.canary.max_candidate_p95_latency_ms
            ),
            max_p95_latency_ratio=self._contract.canary.max_p95_latency_ratio,
        )
        result = self._canary.run_stage(
            experiment_id,
            guardrails,
            control_base_url=control_runtime.base_url,
            candidate_base_url=runtime.base_url,
            operation_id=operation_id,
        )
        return _canary_decision_to_document(result.decision)

    @DBOS.step(
        retries_allowed=True,
        max_attempts=3,
        interval_seconds=1.0,
        backoff_rate=2.0,
    )
    def _restore_canary_control_step(
        self,
        experiment_id: str,
        *,
        operation_id: str,
    ) -> None:
        self._canary.restore_candidate_control(
            experiment_id,
            operation_id=operation_id,
        )

    @DBOS.step(retries_allowed=False)
    def _apply_canary_step(
        self,
        cycle_id: str,
        canary_doc: dict[str, object],
        operation_id: str,
    ) -> dict[str, object]:
        decision = _canary_decision_from_document(canary_doc)
        cycle = self._cycles.get(cycle_id)
        updated = self._releases.apply_canary_decision(
            cycle_id,
            decision,
            expected_version=cycle.version,
            operation_id=operation_id,
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _promote_step(
        self,
        cycle_id: str,
        verification_doc: dict[str, object],
        proposal_doc: dict[str, object],
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        proposal = _proposal_from_document(proposal_doc)
        result = self._releases.decide_and_apply_promotion(
            cycle_id,
            _verification_from_document(verification_doc),
            mandatory_gates=frozenset(proposal.mandatory_gates),
            expected_version=cycle.version,
            operation_id=f"{operation_id}:promotion",
        )
        return _cycle_to_document(result.cycle)

    @DBOS.step(retries_allowed=False)
    def _promote_source_step(
        self,
        candidate_doc: dict[str, object],
    ) -> dict[str, object]:
        candidate = self._source_promotion.promote(
            _candidate_from_document(candidate_doc),
            repository_root=self._repository_root,
            default_branch=self._default_branch,
        )
        return _candidate_to_document(candidate)

    @DBOS.step(retries_allowed=False)
    def _promotion_time_step(self) -> str:
        return datetime.now(UTC).isoformat()

    @DBOS.step(retries_allowed=False)
    def _finalize_release_step(
        self,
        cycle_id: str,
        candidate_doc: dict[str, object],
        artifact_doc: dict[str, object],
        deployment_bundle: dict[str, object],
        promoted_at: str,
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        deployment = _deployment_from_document(
            _mapping(deployment_bundle.get("deployment"), "deployment")
        )
        release = self._finalization.finalize(
            cycle,
            _candidate_from_document(candidate_doc),
            _artifact_from_document(artifact_doc),
            deployment,
            release_id=f"{cycle_id}-release",
            promoted_at=datetime.fromisoformat(promoted_at),
            operation_id=f"{operation_id}:release",
        )
        return {
            "id": release.id,
            "target_id": release.target_id,
            "source_commit": release.source_commit,
            "source_tree": release.source_tree,
            "artifact_digest": release.artifact_digest,
            "objective_revision_id": release.objective_revision_id,
            "deployment_id": release.deployment_id,
            "promoted_at": release.promoted_at.isoformat(),
        }

    def _terminal_failure_result(
        self,
        cycle_id: str,
        operation_id: str,
        *,
        phase: str,
        error: Exception,
    ) -> dict[str, object]:
        cycle_doc = self._fail_terminal_step(
            cycle_id,
            operation_id=f"{operation_id}:failed:{phase}",
        )
        deployment_id = (
            f"{cycle_id}-candidate"
            if phase in {"deployment", "performance", "canary"}
            else None
        )
        self._best_effort_cleanup(cycle_id, deployment_id=deployment_id)
        return _terminal_result(
            cycle_doc,
            f"{phase} failed closed: {type(error).__name__}",
        )

    def _best_effort_cleanup(
        self,
        cycle_id: str,
        *,
        deployment_id: str | None,
    ) -> None:
        with suppress(Exception):
            self._cleanup_step(cycle_id, deployment_id)

    @DBOS.step(
        retries_allowed=True,
        max_attempts=3,
        interval_seconds=1.0,
        backoff_rate=2.0,
    )
    def _cleanup_step(
        self,
        cycle_id: str,
        deployment_id: str | None,
    ) -> None:
        if deployment_id is not None:
            self._deployment.stop(deployment_id)
        self._source_promotion.cleanup_cycle(
            repository_root=self._repository_root,
            worktree_root=self._worktree_root,
            cycle_id=cycle_id,
        )

    @DBOS.step(retries_allowed=False)
    def _fail_terminal_step(
        self,
        cycle_id: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        if cycle.state in {
            CycleState.COMPLETED,
            CycleState.REJECTED,
            CycleState.ROLLED_BACK,
            CycleState.BLOCKED,
            CycleState.CANCELLED,
            CycleState.FAILED_TERMINAL,
        }:
            return _cycle_to_document(cycle)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.FAILED_TERMINAL,
            expected_version=cycle.version,
            operation_id=operation_id,
        )
        return _cycle_to_document(updated)

    @DBOS.step(retries_allowed=False)
    def _reject_step(
        self,
        cycle_id: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        cycle = self._cycles.get(cycle_id)
        if cycle.state is CycleState.REJECTED:
            return _cycle_to_document(cycle)
        updated = self._cycles.transition(
            cycle.id,
            CycleState.REJECTED,
            expected_version=cycle.version,
            operation_id=operation_id,
            release_decision=ReleaseDecisionKind.REJECT,
        )
        return _cycle_to_document(updated)


def _terminal_result(cycle_doc: dict[str, object], reason: str) -> dict[str, object]:
    return {
        "cycle": cycle_doc,
        "release": None,
        "status": _cycle_state_from_document(cycle_doc).value,
        "reason": reason,
    }


def _proposal_to_document(proposal: ChangeProposal) -> dict[str, object]:
    return {
        "id": proposal.id,
        "target_id": proposal.target_id,
        "baseline_release_id": proposal.baseline_release_id,
        "baseline_commit": proposal.baseline_commit,
        "objective_revision_id": proposal.objective_revision_id,
        "diagnosis_id": proposal.diagnosis_id,
        "acceptance_criteria": list(proposal.acceptance_criteria),
        "allowed_paths": list(proposal.allowed_paths),
        "forbidden_paths": list(proposal.forbidden_paths),
        "max_implementation_attempts": proposal.max_implementation_attempts,
        "max_changed_files": proposal.max_changed_files,
        "mandatory_gates": list(proposal.mandatory_gates),
        "change_intent": proposal.change_intent,
    }


def _proposal_from_document(document: dict[str, object]) -> ChangeProposal:
    return ChangeProposal(
        id=_string(document, "id"),
        target_id=_string(document, "target_id"),
        baseline_release_id=_string(document, "baseline_release_id"),
        baseline_commit=_string(document, "baseline_commit"),
        objective_revision_id=_string(document, "objective_revision_id"),
        diagnosis_id=_optional_string(document.get("diagnosis_id")),
        acceptance_criteria=_strings(document, "acceptance_criteria"),
        allowed_paths=_strings(document, "allowed_paths"),
        forbidden_paths=_strings(document, "forbidden_paths"),
        max_implementation_attempts=_integer(document, "max_implementation_attempts"),
        mandatory_gates=_strings(document, "mandatory_gates"),
        change_intent=_optional_string(document.get("change_intent")),
        max_changed_files=_integer(document, "max_changed_files"),
    )


def _candidate_to_document(candidate: CandidateRevision) -> dict[str, object]:
    return {
        "id": candidate.id,
        "cycle_id": candidate.cycle_id,
        "worktree_path": candidate.worktree_path,
        "branch_name": candidate.branch_name,
        "base_commit": candidate.base_commit,
        "candidate_commit": candidate.candidate_commit,
        "tree_hash": candidate.tree_hash,
        "changed_paths": list(candidate.changed_paths),
        "codex_thread_id": candidate.codex_thread_id,
        "implementation_attempt": candidate.implementation_attempt,
    }


def _candidate_from_document(document: dict[str, object]) -> CandidateRevision:
    return CandidateRevision(
        id=_string(document, "id"),
        cycle_id=_string(document, "cycle_id"),
        worktree_path=_string(document, "worktree_path"),
        branch_name=_string(document, "branch_name"),
        base_commit=_string(document, "base_commit"),
        candidate_commit=_string(document, "candidate_commit"),
        tree_hash=_string(document, "tree_hash"),
        changed_paths=_strings(document, "changed_paths"),
        codex_thread_id=_string(document, "codex_thread_id"),
        implementation_attempt=_integer(document, "implementation_attempt"),
    )


def _verification_to_document(run: VerificationRun) -> dict[str, object]:
    return {
        "id": run.id,
        "candidate_id": run.candidate_id,
        "checks": [_check_to_document(check) for check in run.checks],
    }


def _verification_from_document(document: dict[str, object]) -> VerificationRun:
    raw_checks = document.get("checks")
    if not isinstance(raw_checks, list):
        raise ValueError("verification checks must be an array")
    return VerificationRun(
        id=_string(document, "id"),
        candidate_id=_string(document, "candidate_id"),
        checks=tuple(
            _check_from_document(_mapping(item, "verification check"))
            for item in raw_checks
        ),
    )


def _check_to_document(check: VerificationCheck) -> dict[str, object]:
    return {
        "id": check.id,
        "gate": check.gate,
        "status": check.status.value,
        "started_at": check.started_at.isoformat(),
        "ended_at": check.ended_at.isoformat(),
        "evidence_refs": list(check.evidence_refs),
        "measurements": dict(check.measurements),
    }


def _check_from_document(document: dict[str, object]) -> VerificationCheck:
    measurements = document.get("measurements")
    if not isinstance(measurements, dict):
        raise ValueError("verification measurements must be an object")
    normalized: dict[str, float | int | str | bool] = {}
    for key, value in measurements.items():
        if not isinstance(key, str) or not isinstance(value, (float, int, str, bool)):
            raise ValueError("verification measurements contain unsupported values")
        normalized[key] = value
    return VerificationCheck(
        id=_string(document, "id"),
        gate=_string(document, "gate"),
        status=VerificationStatus(_string(document, "status")),
        started_at=datetime.fromisoformat(_string(document, "started_at")),
        ended_at=datetime.fromisoformat(_string(document, "ended_at")),
        evidence_refs=_strings(document, "evidence_refs"),
        measurements=normalized,
    )


def _artifact_to_document(artifact: BuildArtifact) -> dict[str, object]:
    return {
        "id": artifact.id,
        "candidate_id": artifact.candidate_id,
        "image_digest": artifact.image_digest,
        "source_tree_hash": artifact.source_tree_hash,
        "build_definition_digest": artifact.build_definition_digest,
        "dependency_lock_digest": artifact.dependency_lock_digest,
        "build_evidence_ref": artifact.build_evidence_ref,
        "sbom_digest": artifact.sbom_digest,
        "sbom_ref": artifact.sbom_ref,
        "vulnerability_scan_ref": artifact.vulnerability_scan_ref,
    }


def _artifact_from_document(document: dict[str, object]) -> BuildArtifact:
    return BuildArtifact(
        id=_string(document, "id"),
        candidate_id=_string(document, "candidate_id"),
        image_digest=_string(document, "image_digest"),
        source_tree_hash=_string(document, "source_tree_hash"),
        build_definition_digest=_string(document, "build_definition_digest"),
        dependency_lock_digest=_string(document, "dependency_lock_digest"),
        build_evidence_ref=_string(document, "build_evidence_ref"),
        sbom_digest=_string(document, "sbom_digest"),
        sbom_ref=_string(document, "sbom_ref"),
        vulnerability_scan_ref=_string(document, "vulnerability_scan_ref"),
    )


def _runtime_to_document(runtime: DeploymentRuntime) -> dict[str, object]:
    return {
        "deployment_id": runtime.deployment_id,
        "container_id": runtime.container_id,
        "base_url": runtime.base_url,
        "evidence_ref": runtime.evidence_ref,
    }


def _runtime_from_document(document: dict[str, object]) -> DeploymentRuntime:
    return DeploymentRuntime(
        deployment_id=_string(document, "deployment_id"),
        container_id=_string(document, "container_id"),
        base_url=_string(document, "base_url"),
        evidence_ref=_string(document, "evidence_ref"),
    )


def _deployment_to_document(deployment: Deployment) -> dict[str, object]:
    return {
        "id": deployment.id,
        "target_id": deployment.target_id,
        "artifact_id": deployment.artifact_id,
        "environment": deployment.environment,
        "state": deployment.state.value,
        "observed_at": (
            deployment.observed_at.isoformat() if deployment.observed_at is not None else None
        ),
        "observation_refs": list(deployment.observation_refs),
    }


def _deployment_from_document(document: dict[str, object]) -> Deployment:
    observed_raw = document.get("observed_at")
    observed_at = (
        datetime.fromisoformat(observed_raw)
        if isinstance(observed_raw, str) and observed_raw
        else None
    )
    return Deployment(
        id=_string(document, "id"),
        target_id=_string(document, "target_id"),
        artifact_id=_string(document, "artifact_id"),
        environment=_string(document, "environment"),
        state=DeploymentState(_string(document, "state")),
        observed_at=observed_at,
        observation_refs=_strings(document, "observation_refs"),
    )


def _canary_decision_to_document(decision: CanaryStageDecision) -> dict[str, object]:
    return {
        "kind": decision.kind.value,
        "experiment_id": decision.experiment_id,
        "stage_index": decision.stage_index,
        "next_stage_index": decision.next_stage_index,
        "evidence_refs": list(decision.evidence_refs),
        "violated_guardrails": list(decision.violated_guardrails),
        "reason": decision.reason,
    }


def _canary_decision_from_document(document: dict[str, object]) -> CanaryStageDecision:
    next_stage = document.get("next_stage_index")
    if next_stage is not None and (
        not isinstance(next_stage, int) or isinstance(next_stage, bool)
    ):
        raise ValueError("next canary stage index must be an integer or null")
    return CanaryStageDecision(
        kind=CanaryDecisionKind(_string(document, "kind")),
        experiment_id=_string(document, "experiment_id"),
        stage_index=_integer(document, "stage_index"),
        next_stage_index=next_stage,
        evidence_refs=_strings(document, "evidence_refs"),
        violated_guardrails=_strings(document, "violated_guardrails"),
        reason=_string(document, "reason"),
    )


def _cycle_to_document(cycle: DevelopmentCycle) -> dict[str, object]:
    return {
        "id": cycle.id,
        "target_id": cycle.target_id,
        "state": cycle.state.value,
        "version": cycle.version,
        "evidence_window_id": cycle.evidence_window_id,
        "diagnosis_id": cycle.diagnosis_id,
        "change_proposal_id": cycle.change_proposal_id,
        "candidate_id": cycle.candidate_id,
        "verification_run_id": cycle.verification_run_id,
        "artifact_id": cycle.artifact_id,
        "candidate_deployment_id": cycle.candidate_deployment_id,
        "experiment_id": cycle.experiment_id,
        "release_decision": (
            cycle.release_decision.value if cycle.release_decision is not None else None
        ),
    }


def _cycle_state_from_document(document: dict[str, object]) -> CycleState:
    return CycleState(_string(document, "state"))


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return {str(key): item for key, item in value.items()}


def _string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional string field is invalid")
    return value


def _integer(document: dict[str, object], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _strings(document: dict[str, object], key: str) -> tuple[str, ...]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a string array")
    return tuple(value)
