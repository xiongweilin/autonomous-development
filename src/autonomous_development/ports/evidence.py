from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    ref: str
    sha256: str
    media_type: str
    size_bytes: int


class EvidenceStore(Protocol):
    def put_text(
        self,
        *,
        namespace: str,
        content: str,
        media_type: str = "text/plain; charset=utf-8",
    ) -> EvidenceRecord: ...
