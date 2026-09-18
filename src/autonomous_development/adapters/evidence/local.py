from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from autonomous_development.ports.evidence import EvidenceStore

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LocalEvidenceStore(EvidenceStore):
    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("evidence root must be absolute")
        self._root = root

    def write_json(
        self,
        category: str,
        name: str,
        payload: Mapping[str, object],
    ) -> str:
        safe_category = _safe(category)
        safe_name = _safe(name)
        directory = self._root / safe_category
        directory.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        target = directory / f"{safe_name}-{digest}.json"

        if target.exists():
            if target.read_bytes() != encoded:
                raise RuntimeError("evidence digest collision or mutated evidence")
            return f"file:{target}#sha256:{digest}"

        temporary = directory / f".{safe_name}.{uuid4().hex}.tmp"
        temporary.write_bytes(encoded)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != encoded:
                raise RuntimeError("evidence digest collision or mutated evidence")
        finally:
            temporary.unlink(missing_ok=True)
        return f"file:{target}#sha256:{digest}"


def _safe(value: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"unsafe evidence path component: {value!r}")
    return value
