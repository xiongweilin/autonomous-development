from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class EvidenceStore(Protocol):
    def write_json(
        self,
        category: str,
        name: str,
        payload: Mapping[str, object],
    ) -> str: ...
