import json
from pathlib import Path

import pytest

from autonomous_development.adapters.evidence.local import LocalEvidenceStore


def _path_from_ref(ref: str) -> Path:
    path_part, _, _ = ref.removeprefix("file:").partition("#sha256:")
    return Path(path_part)


def test_evidence_is_content_addressed_and_immutable(tmp_path: Path) -> None:
    store = LocalEvidenceStore(tmp_path.resolve())
    ref = store.write_json("quality", "gate-1", {"status": "passed", "count": 2})
    path = _path_from_ref(ref)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "count": 2,
        "status": "passed",
    }
    assert path.name.startswith("gate-1-")
    assert "#sha256:" in ref

    same_ref = store.write_json("quality", "gate-1", {"status": "passed", "count": 2})
    changed_ref = store.write_json("quality", "gate-1", {"status": "failed", "count": 2})
    assert same_ref == ref
    assert changed_ref != ref
    assert _path_from_ref(ref).read_text(encoding="utf-8") != _path_from_ref(
        changed_ref
    ).read_text(encoding="utf-8")


def test_unsafe_evidence_component_is_rejected(tmp_path: Path) -> None:
    store = LocalEvidenceStore(tmp_path.resolve())
    with pytest.raises(ValueError, match="unsafe"):
        store.write_json("../escape", "gate", {"ok": True})
