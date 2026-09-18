import json
from pathlib import Path

import pytest

from autonomous_development.adapters.evidence.local import LocalEvidenceStore


def test_evidence_is_written_with_content_digest(tmp_path: Path) -> None:
    store = LocalEvidenceStore(tmp_path.resolve())
    ref = store.write_json("quality", "gate-1", {"status": "passed", "count": 2})
    path = tmp_path / "quality" / "gate-1.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "count": 2,
        "status": "passed",
    }
    assert ref.startswith(f"file:{path}#sha256:")


def test_unsafe_evidence_component_is_rejected(tmp_path: Path) -> None:
    store = LocalEvidenceStore(tmp_path.resolve())
    with pytest.raises(ValueError, match="unsafe"):
        store.write_json("../escape", "gate", {"ok": True})
