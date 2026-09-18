from autonomous_development.adapters.file_evidence import FileEvidenceStore


def test_file_evidence_is_content_addressed_and_idempotent(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path)
    first = store.put_text(namespace="gate", content="same evidence")
    second = store.put_text(namespace="gate", content="same evidence")

    assert first == second
    assert first.ref.startswith("file-evidence:gate:")
    assert len(list((tmp_path / "gate").iterdir())) == 1
