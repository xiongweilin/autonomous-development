from __future__ import annotations

import hashlib
import importlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.traffic import (
    TrafficDirector,
    TrafficRouteSnapshot,
    TrafficRouteState,
    TrafficSplit,
)


class TrafficOperationConflict(ValueError):
    pass


class AtomicFileTrafficDirector(TrafficDirector):
    def __init__(self, root: Path, evidence: EvidenceStore) -> None:
        if not root.is_absolute():
            raise ValueError("traffic state root must be absolute")
        self._root = root
        self._evidence = evidence
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "operations").mkdir(parents=True, exist_ok=True)

    def apply(self, split: TrafficSplit) -> TrafficRouteState:
        _require_loopback(split.control_base_url)
        _require_loopback(split.candidate_base_url)
        with self._lock():
            receipt_path = self._receipt_path(split.operation_id)
            if receipt_path.exists():
                return self._replay_receipt(receipt_path, split)

            current = self._read_current()
            if current is not None and current.get("operation_id") == split.operation_id:
                _validate_current_matches(current, split)
                state = _state_from_document(current)
                self._write_receipt(receipt_path, split, state)
                return state

            generation = int(current["generation"]) + 1 if current is not None else 1
            evidence_ref = self._evidence.write_json(
                "traffic-effect",
                _safe_evidence_name(split.operation_id),
                {
                    "experiment_id": split.experiment_id,
                    "stage_index": split.stage_index,
                    "control_base_url": split.control_base_url,
                    "candidate_base_url": split.candidate_base_url,
                    "candidate_weight_percent": split.candidate_weight_percent,
                    "operation_id": split.operation_id,
                    "generation": generation,
                    "target_id": split.target_id,
                    "control_release_id": split.control_release_id,
                    "candidate_deployment_id": split.candidate_deployment_id,
                },
            )
            state = TrafficRouteState(
                experiment_id=split.experiment_id,
                stage_index=split.stage_index,
                candidate_weight_percent=split.candidate_weight_percent,
                generation=generation,
                evidence_ref=evidence_ref,
                target_id=split.target_id,
                control_release_id=split.control_release_id,
                candidate_deployment_id=split.candidate_deployment_id,
            )
            document = {
                **asdict(split),
                "generation": generation,
                "evidence_ref": evidence_ref,
            }
            self._atomic_write_json(self._root / "current.json", document)
            self._write_receipt(receipt_path, split, state)
            return state

    def restore_control(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> TrafficRouteState:
        current = self.read_current()
        return self.apply(
            TrafficSplit(
                experiment_id=experiment_id,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                candidate_weight_percent=0,
                operation_id=operation_id,
                target_id=current.target_id if current is not None else None,
                control_release_id=(
                    current.control_release_id if current is not None else None
                ),
                candidate_deployment_id=(
                    current.candidate_deployment_id if current is not None else None
                ),
            )
        )

    def read_current(self) -> TrafficRouteSnapshot | None:
        with self._lock():
            current = self._read_current()
            return _snapshot_from_document(current) if current is not None else None

    def _replay_receipt(
        self,
        receipt_path: Path,
        split: TrafficSplit,
    ) -> TrafficRouteState:
        document = _read_json(receipt_path)
        stored = document.get("split")
        if not isinstance(stored, dict):
            raise RuntimeError("traffic operation receipt has invalid split payload")
        _validate_current_matches(stored, split)
        state = document.get("state")
        if not isinstance(state, dict):
            raise RuntimeError("traffic operation receipt has invalid state payload")
        return _state_from_document(state)

    def _write_receipt(
        self,
        receipt_path: Path,
        split: TrafficSplit,
        state: TrafficRouteState,
    ) -> None:
        self._atomic_write_json(
            receipt_path,
            {
                "split": asdict(split),
                "state": asdict(state),
            },
        )

    def _read_current(self) -> dict[str, Any] | None:
        path = self._root / "current.json"
        if not path.exists():
            return None
        return _read_json(path)

    def _receipt_path(self, operation_id: str) -> Path:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self._root / "operations" / f"{digest}.json"

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        temporary.write_bytes(encoded)
        os.replace(temporary, path)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock_path = self._root / ".route.lock"
        with lock_path.open("a+b") as handle:
            _acquire_file_lock(handle)
            try:
                yield
            finally:
                _release_file_lock(handle)


def _read_json(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError(f"traffic state is not a JSON object: {path}")
    return {str(key): value for key, value in parsed.items()}


def _validate_current_matches(document: dict[str, Any], split: TrafficSplit) -> None:
    expected = asdict(split)
    for key, value in expected.items():
        if document.get(key) != value:
            raise TrafficOperationConflict(
                f"operation id {split.operation_id} is already bound to different traffic state"
            )


def _state_from_document(document: dict[str, Any]) -> TrafficRouteState:
    try:
        return TrafficRouteState(
            experiment_id=str(document["experiment_id"]),
            stage_index=int(document["stage_index"]),
            candidate_weight_percent=int(document["candidate_weight_percent"]),
            generation=int(document["generation"]),
            evidence_ref=str(document["evidence_ref"]),
            target_id=_optional_string(document.get("target_id")),
            control_release_id=_optional_string(document.get("control_release_id")),
            candidate_deployment_id=_optional_string(
                document.get("candidate_deployment_id")
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("traffic route state is malformed") from exc


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _safe_evidence_name(operation_id: str) -> str:
    return hashlib.sha256(operation_id.encode("utf-8")).hexdigest()


def _require_loopback(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("V1 traffic routing is restricted to local HTTP loopback")



def _snapshot_from_document(document: dict[str, Any]) -> TrafficRouteSnapshot:
    try:
        return TrafficRouteSnapshot(
            experiment_id=str(document["experiment_id"]),
            stage_index=int(document["stage_index"]),
            control_base_url=str(document["control_base_url"]),
            candidate_base_url=str(document["candidate_base_url"]),
            candidate_weight_percent=int(document["candidate_weight_percent"]),
            operation_id=str(document["operation_id"]),
            generation=int(document["generation"]),
            evidence_ref=str(document["evidence_ref"]),
            target_id=_optional_string(document.get("target_id")),
            control_release_id=_optional_string(document.get("control_release_id")),
            candidate_deployment_id=_optional_string(
                document.get("candidate_deployment_id")
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("traffic route snapshot is malformed") from exc



def _acquire_file_lock(handle: Any) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        module = importlib.import_module("msvcrt")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + 30.0
        while True:
            try:
                module.locking(handle.fileno(), module.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out acquiring traffic state lock") from None
                time.sleep(0.05)

    module = importlib.import_module("fcntl")
    module.flock(handle.fileno(), module.LOCK_EX)


def _release_file_lock(handle: Any) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        module = importlib.import_module("msvcrt")
        handle.seek(0)
        module.locking(handle.fileno(), module.LK_UNLCK, 1)
        return

    module = importlib.import_module("fcntl")
    module.flock(handle.fileno(), module.LOCK_UN)
