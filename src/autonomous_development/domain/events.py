from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class DomainEvent:
    id: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    occurred_at: datetime
    payload: Mapping[str, str | int | float | bool | None]
