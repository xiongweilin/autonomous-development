from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Literal

from autonomous_development.ports.traffic import TrafficRouteSnapshot

Arm = Literal["control", "candidate"]


@dataclass(slots=True)
class _ArmMetrics:
    requests: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    first_seen: float | None = None
    last_seen: float | None = None
    dropped_samples: int = 0


@dataclass(slots=True)
class _GenerationMetrics:
    experiment_id: str
    stage_index: int
    control: _ArmMetrics = field(default_factory=_ArmMetrics)
    candidate: _ArmMetrics = field(default_factory=_ArmMetrics)


class CanaryMetricsRegistry:
    def __init__(self, *, max_latency_samples_per_arm: int = 100_000) -> None:
        if max_latency_samples_per_arm < 100:
            raise ValueError("latency sample capacity must be at least 100")
        self._max_samples = max_latency_samples_per_arm
        self._lock = asyncio.Lock()
        self._generations: dict[int, _GenerationMetrics] = {}

    async def record(
        self,
        route: TrafficRouteSnapshot,
        arm: Arm,
        *,
        status_code: int,
        latency_ms: float,
    ) -> None:
        if latency_ms < 0:
            raise ValueError("latency cannot be negative")
        now = time.monotonic()
        async with self._lock:
            generation = self._generations.setdefault(
                route.generation,
                _GenerationMetrics(
                    experiment_id=route.experiment_id,
                    stage_index=route.stage_index,
                ),
            )
            if (
                generation.experiment_id != route.experiment_id
                or generation.stage_index != route.stage_index
            ):
                raise RuntimeError("traffic generation identity changed while collecting metrics")
            metrics = generation.candidate if arm == "candidate" else generation.control
            metrics.requests += 1
            if status_code >= 500:
                metrics.errors += 1
            metrics.first_seen = now if metrics.first_seen is None else metrics.first_seen
            metrics.last_seen = now
            if len(metrics.latencies_ms) < self._max_samples:
                metrics.latencies_ms.append(latency_ms)
            else:
                metrics.dropped_samples += 1

    async def snapshot(self, generation: int) -> dict[str, object] | None:
        async with self._lock:
            metrics = self._generations.get(generation)
            if metrics is None:
                return None
            first_seen = _minimum_time(
                metrics.control.first_seen,
                metrics.candidate.first_seen,
            )
            last_seen = _maximum_time(
                metrics.control.last_seen,
                metrics.candidate.last_seen,
            )
            duration = (
                max(0, int(last_seen - first_seen))
                if first_seen is not None and last_seen is not None
                else 0
            )
            return {
                "generation": generation,
                "experiment_id": metrics.experiment_id,
                "stage_index": metrics.stage_index,
                "observed_duration_seconds": duration,
                "control": _arm_snapshot(metrics.control),
                "candidate": _arm_snapshot(metrics.candidate),
            }


def _arm_snapshot(metrics: _ArmMetrics) -> dict[str, object]:
    error_rate = metrics.errors / metrics.requests if metrics.requests else 0.0
    return {
        "requests": metrics.requests,
        "errors": metrics.errors,
        "error_rate": error_rate,
        "p95_latency_ms": _p95(metrics.latencies_ms),
        "dropped_samples": metrics.dropped_samples,
    }


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def _minimum_time(left: float | None, right: float | None) -> float | None:
    values = tuple(value for value in (left, right) if value is not None)
    return min(values) if values else None


def _maximum_time(left: float | None, right: float | None) -> float | None:
    values = tuple(value for value in (left, right) if value is not None)
    return max(values) if values else None
