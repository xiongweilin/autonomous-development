from enum import StrEnum


class CycleState(StrEnum):
    NEW = "new"
    BASELINE_VERIFIED = "baseline-verified"
    EVIDENCE_READY = "evidence-ready"
    DIAGNOSED = "diagnosed"
    CHANGE_PROPOSED = "change-proposed"
    DEVELOPING = "developing"
    CANDIDATE_READY = "candidate-ready"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    BUILT = "built"
    STAGED = "staged"
    CANARYING = "canarying"
    PROMOTION_READY = "promotion-ready"
    PROMOTED = "promoted"
    SOAKING = "soaking"
    COMPLETED = "completed"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled-back"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED_RECOVERABLE = "failed-recoverable"
    FAILED_TERMINAL = "failed-terminal"


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class DeploymentState(StrEnum):
    PLANNED = "planned"
    STARTING = "starting"
    READY = "ready"
    SERVING = "serving"
    DRAINING = "draining"
    STOPPED = "stopped"
    FAILED = "failed"


class CanaryDecisionKind(StrEnum):
    ADVANCE = "advance"
    HOLD = "hold"
    ROLLBACK = "rollback"
    PROMOTION_READY = "promotion-ready"


class SoakDecisionKind(StrEnum):
    HOLD = "hold"
    ROLLBACK = "rollback"
    COMPLETE = "complete"


class ReleaseDecisionKind(StrEnum):
    PROMOTE = "promote"
    REJECT = "reject"
    ROLLBACK = "rollback"
    HOLD_INSUFFICIENT_EVIDENCE = "hold-insufficient-evidence"
    BLOCKED = "blocked"


class FeedbackKind(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
