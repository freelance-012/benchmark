"""Atomic storage for run configuration, receipts, outputs, and checkpoints."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import yaml

from ..algorithms.contracts import AlgorithmContract
from ..datasets.models import DatasetInstance
from .models import (
    RUN_CONFIG_SCHEMA_VERSION,
    DatasetRunReceipt,
    RunCheckpoint,
    RunIssue,
    RunRequest,
    SegmentPaths,
    SegmentRunReceipt,
)

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RunStorageError(Exception):
    """Run facts cannot be persisted safely."""


class RunStore:
    def freeze_configuration(
        self,
        test_root: Path,
        request: RunRequest,
        contract: AlgorithmContract,
        git_commit: str,
        instances: Sequence[DatasetInstance],
        issues: Sequence[RunIssue],
    ) -> None:
        config_root = test_root / "config"
        algorithm_payload = {
            "schema_version": RUN_CONFIG_SCHEMA_VERSION,
            "algorithm": request.build_config.algorithm_id,
            "build": {
                "algorithm_path": str(request.build_config.algorithm_path),
                "script_path": str(request.build_config.script_path),
            },
            "contract": contract.to_dict(),
        }
        run_payload = {
            "schema_version": RUN_CONFIG_SCHEMA_VERSION,
            "test_id": test_root.name,
            "git_commit": git_commit,
            "failure_policy": request.failure_policy,
            "failure_threshold": request.failure_threshold,
            "timeout_seconds": request.timeout_seconds,
            "dataset_configs": [
                {
                    "root_path": str(item.root_path),
                    "dataset_type": item.dataset_type,
                }
                for item in request.dataset_configs
            ],
            "selected_dataset_paths": [
                str(Path(item).expanduser().resolve())
                for item in request.selected_dataset_paths
            ],
            "dataset_order": [
                {
                    "dataset_id": item.dataset_id,
                    "dataset_type": item.dataset_type,
                    "root_path": str(item.root_path),
                }
                for item in instances
            ],
            "preflight_issues": [item.to_dict() for item in issues],
        }
        self.save_mapping(config_root / "algorithm.yaml", algorithm_payload)
        self.save_mapping(config_root / "run.yaml", run_payload)
        for instance in instances:
            filename = f"{_safe_component(instance.dataset_id, 'dataset_id')}.yaml"
            self.save_mapping(config_root / "datasets" / filename, instance.to_dict())

    def load_mapping(self, path: Path) -> Dict[str, Any]:
        try:
            payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("document root must be a mapping")
            return payload
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise RunStorageError(f"cannot load YAML {path}: {exc}") from exc

    def save_mapping(self, path: Path, payload: Mapping[str, Any]) -> None:
        temporary: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                yaml.safe_dump(
                    dict(payload),
                    handle,
                    allow_unicode=True,
                    sort_keys=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, yaml.YAMLError) as exc:
            raise RunStorageError(f"cannot save YAML {path}: {exc}") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def save_checkpoint(self, test_root: Path, checkpoint: RunCheckpoint) -> Path:
        path = test_root / "checkpoint.yaml"
        self.save_mapping(path, checkpoint.to_dict())
        return path

    def load_checkpoint(self, test_root: Path) -> RunCheckpoint:
        try:
            return RunCheckpoint.from_dict(
                self.load_mapping(test_root / "checkpoint.yaml")
            )
        except ValueError as exc:
            raise RunStorageError(f"invalid run checkpoint: {exc}") from exc

    def segment_paths(
        self,
        test_root: Path,
        dataset_id: str,
        segment_id: str,
    ) -> SegmentPaths:
        dataset_component = _safe_component(dataset_id, "dataset_id")
        segment_component = _safe_component(segment_id, "segment_id")
        run_dir = (
            test_root
            / "datasets"
            / dataset_component
            / "segments"
            / segment_component
            / "run"
        )
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            result_dir = run_dir / "result"
            result_dir.mkdir()
        except OSError as exc:
            raise RunStorageError(
                f"cannot create Segment result directory {run_dir}: {exc}"
            ) from exc
        return SegmentPaths(
            run_dir=run_dir,
            receipt_path=run_dir / "receipt.yaml",
            stdout_path=run_dir / "stdout.log",
            stderr_path=run_dir / "stderr.log",
            result_dir=result_dir,
        )

    def save_segment_receipt(
        self,
        paths: SegmentPaths,
        receipt: SegmentRunReceipt,
    ) -> Path:
        self.save_mapping(paths.receipt_path, receipt.to_dict())
        return paths.receipt_path

    def save_dataset_receipt(
        self,
        test_root: Path,
        receipt: DatasetRunReceipt,
    ) -> Path:
        dataset_component = _safe_component(receipt.dataset_id, "dataset_id")
        path = test_root / "datasets" / dataset_component / "dataset_receipt.yaml"
        self.save_mapping(path, receipt.to_dict())
        return path

    def copy_fixed_output(
        self,
        source: Path,
        result_dir: Path,
        relative_path: Path,
    ) -> Path:
        return self.copy_result_file(source, result_dir, relative_path)

    def copy_result_file(
        self,
        source: Path,
        result_dir: Path,
        relative_path: Path,
    ) -> Path:
        if relative_path.is_absolute():
            raise RunStorageError(f"result file path must be relative: {relative_path}")
        destination = (result_dir / relative_path).resolve()
        if not _is_within(destination, result_dir.resolve()):
            raise RunStorageError(
                f"result file path escapes result directory: {destination}"
            )
        if destination.exists():
            raise RunStorageError(f"result file already exists: {destination}")

        temporary: Optional[Path] = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as source_handle:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination.parent,
                    prefix=f".{destination.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as output_handle:
                    temporary = Path(output_handle.name)
                    shutil.copyfileobj(source_handle, output_handle)
                    output_handle.flush()
                    os.fsync(output_handle.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            raise RunStorageError(
                f"cannot copy result file to {destination}: {exc}"
            ) from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
        return destination

    def archive_incomplete_dataset(self, test_root: Path, dataset_id: str) -> None:
        dataset_component = _safe_component(dataset_id, "dataset_id")
        dataset_root = test_root / "datasets" / dataset_component
        movable = [
            item
            for item in (
                dataset_root / "segments",
                dataset_root / "dataset_receipt.yaml",
            )
            if item.exists()
        ]
        if not movable:
            return

        attempts_root = dataset_root / "previous_attempts"
        try:
            attempts_root.mkdir(parents=True, exist_ok=True)
            index = 1
            while (attempts_root / f"attempt-{index:03d}").exists():
                index += 1
            attempt_root = attempts_root / f"attempt-{index:03d}"
            attempt_root.mkdir()
            for item in movable:
                os.replace(item, attempt_root / item.name)
            self._rewrite_archived_receipt_paths(dataset_root, attempt_root)
        except OSError as exc:
            raise RunStorageError(
                f"cannot archive incomplete dataset {dataset_id}: {exc}"
            ) from exc

    def _rewrite_archived_receipt_paths(
        self,
        original_dataset_root: Path,
        attempt_root: Path,
    ) -> None:
        archived_segments = attempt_root / "segments"
        if not archived_segments.is_dir():
            return
        original_segments = original_dataset_root / "segments"
        for receipt_path in archived_segments.rglob("receipt.yaml"):
            payload = self.load_mapping(receipt_path)
            changed = False
            for key in ("stdout_path", "stderr_path", "output_result_path"):
                raw_value = payload.get(key)
                if raw_value is None:
                    continue
                path = Path(str(raw_value))
                try:
                    relative = path.relative_to(original_segments)
                except ValueError:
                    continue
                payload[key] = str(archived_segments / relative)
                changed = True
            if changed:
                self.save_mapping(receipt_path, payload)


def _safe_component(value: str, label: str) -> str:
    text = str(value)
    if text in {".", ".."} or not _SAFE_COMPONENT.fullmatch(text):
        raise RunStorageError(f"{label} is not safe for a result path: {value!r}")
    return text


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
