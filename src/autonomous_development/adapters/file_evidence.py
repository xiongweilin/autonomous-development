from __future__ import annotations

import hashlib
from pathlib import Path

from autonomous_development.ports.evidence import EvidenceRecord, EvidenceStore


class FileEvidenceStore(EvidenceStore):
    """Content-addressed immutable evidence store on the local filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put_text(
        self,
        *,
        namespace: str,
        content: str,
        media_type: str = "text/plain; charset=utf-8",
    ) -> EvidenceRecord:
        safe_namespace = _safe_namespace(namespace)
        payload = content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        directory = self._root / safe_namespace
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.txt"
        if path.exists():
            if path.read_bytes() != payload:
                raise RuntimeError("content-addressed evidence collision")
        else:
            path.write_bytes(payload)
        return EvidenceRecord(
            ref=f"file-evidence:{safe_namespace}:{digest}",
            sha256=digest,
            media_type=media_type,
            size_bytes=len(payload),
        )


def _safe_namespace(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise ValueError("evidence namespace must be one safe path component")
    return normalized
