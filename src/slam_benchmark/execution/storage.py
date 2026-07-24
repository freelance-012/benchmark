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
            "segment_order": [
                {
                    "run_index": run_index,
                    "dataset_id": instance.dataset_id,
                    "segment_id": segment.segment_id,
                    "start_timestamp": segment.start_timestamp,
                    "end_timestamp": segment.end_timestamp,
                }
                for run_index, (instance, segment) in enumerate(
                    (
                        (instance, segment)
                        for instance in instances
                        for segment in sorted(
                            (item for item in instance.segments if item.valid),
                            key=lambda item: item.sequence_no,
                        )
                    )
                )
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
        run_index: int,
    ) -> SegmentPaths:
        if run_index < 0:
            raise RunStorageError(f"run_index must not be negative: {run_index}")
        segment_dir = test_root / "dataset" / str(run_index)
        evaluation_dir = segment_dir / "evaluation"
        try:
            segment_dir.mkdir(parents=True, exist_ok=False)
            evaluation_dir.mkdir()
        except OSError as exc:
            raise RunStorageError(
                f"cannot create Segment result directory {segment_dir}: {exc}"
            ) from exc
        return SegmentPaths(
            segment_dir=segment_dir,
            receipt_path=segment_dir / "receipt.yaml",
            stdout_path=segment_dir / "stdout.log",
            stderr_path=segment_dir / "stderr.log",
            evaluation_dir=evaluation_dir,
        )

    def save_segment_receipt(
        self,
        paths: SegmentPaths,
        receipt: SegmentRunReceipt,
    ) -> Path:
        self.save_mapping(paths.receipt_path, receipt.to_dict())
        return paths.receipt_path

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

    def reset_segment_directories(
        self,
        test_root: Path,
        run_indexes: Sequence[int],
    ) -> None:
        """Remove only incomplete Segment directories before a safe resume."""

        dataset_root = test_root / "dataset"
        for run_index in run_indexes:
            if run_index < 0:
                raise RunStorageError(f"run_index must not be negative: {run_index}")
            segment_dir = dataset_root / str(run_index)
            if not segment_dir.exists():
                continue
            if segment_dir.is_symlink() or not segment_dir.is_dir():
                raise RunStorageError(
                    f"cannot reset invalid Segment result path: {segment_dir}"
                )
            try:
                shutil.rmtree(segment_dir)
            except OSError as exc:
                raise RunStorageError(
                    f"cannot reset Segment result directory {segment_dir}: {exc}"
                ) from exc


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
