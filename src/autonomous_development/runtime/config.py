from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTODEV_",
        extra="forbid",
        case_sensitive=False,
    )

    database_url: SecretStr
    dbos_system_database_url: SecretStr
    state_root: Path

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8765, ge=1, le=65535)
    canary_proxy_base_url: str = "http://127.0.0.1:8765/product"
    prometheus_base_url: str = "http://127.0.0.1:19090"
    telemetry_queries: dict[str, str] = Field(default_factory=dict)

    feedback_schedule: str = "*/5 * * * *"
    schedule_timezone: str = "UTC"
    minimum_feedback_severity: int = Field(default=3, ge=0, le=5)
    diagnosis_delay_seconds: int = Field(default=300, ge=0)
    evidence_lookback_seconds: int = Field(default=900, ge=0)
    minimum_diagnosis_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    canary_hold_sleep_seconds: float = Field(default=30.0, gt=0)
    soak_hold_sleep_seconds: float = Field(default=30.0, gt=0)
    canary_observation_timeout_seconds: int = Field(default=900, ge=1)

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        return cls()  # type: ignore[call-arg]

    @field_validator("state_root", mode="before")
    @classmethod
    def validate_state_root(cls, value: object) -> Path:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            raise ValueError("state_root must be an absolute path")
        return path.resolve()

    @field_validator("api_host")
    @classmethod
    def validate_api_host(cls, value: str) -> str:
        if value != "127.0.0.1":
            raise ValueError("V1 control API must bind to 127.0.0.1")
        return value

    @field_validator("canary_proxy_base_url", "prometheus_base_url")
    @classmethod
    def validate_loopback_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("runtime service URL must use http or https")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("V1 runtime service URL must use loopback")
        if not parsed.port:
            raise ValueError("runtime service URL must include an explicit port")
        return value.rstrip("/")

    @field_validator("telemetry_queries")
    @classmethod
    def validate_telemetry_queries(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not query.strip() for key, query in value.items()):
            raise ValueError("telemetry query names and expressions must be non-empty")
        return value

    @property
    def evidence_root(self) -> Path:
        return self.state_root / "evidence"

    @property
    def traffic_state_root(self) -> Path:
        return self.state_root / "traffic"

    @property
    def worktree_root(self) -> Path:
        return self.state_root / "worktrees"

    @property
    def codex_thread_journal_root(self) -> Path:
        return self.state_root / "codex-threads"
