"""Data structures for algorithm execution and dataset-level recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..compilation.models import BuildConfig
from ..datasets.models import DatasetScanConfig

RUN_RECEIPT_SCHEMA_VERSION = 1
RUN_CHECKPOINT_SCHEMA_VERSION = 1
RUN_CONFIG_SCHEMA_VERSION = 1

FAILURE_POLICY_CONTINUE = "continue"
FAILURE_POLICY_FAIL_FAST = "fail_fast"
FAILURE_POLICIES = (FAILURE_POLICY_CONTINUE, FAILURE_POLICY_FAIL_FAST)


@dataclass(frozen=True)
class RunRequest:
    build_config: BuildConfig
    dataset_configs: Tuple[DatasetScanConfig, ...]
    selected_dataset_paths: Tuple[Path, ...] = ()
    failure_policy: str = FAILURE_POLICY_CONTINUE
    failure_threshold: int = 1
    timeout_seconds: float = 30 * 60.0
    results_root: Path = Path("results")


@dataclass(frozen=True)
class RunIssue:
    code: str
    path: Path
    message: str
    dataset_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "path": str(self.path),
            "dataset_id": self.dataset_id,
            "status": "not_run",
            "message": self.message,
        }


@dataclass(frozen=True)
class ResolvedRunCommand:
    argv: Tuple[str, ...]
    input_arguments: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class ProcessResult:
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: Optional[int]
    failure_reason: Optional[str]


@dataclass(frozen=True)
class SegmentPaths:
    run_dir: Path
    receipt_path: Path
    stdout_path: Path
    stderr_path: Path
    result_dir: Path


@dataclass(frozen=True)
class SegmentRunReceipt:
    test_id: str
    algorithm_id: str
    contract_version: int
    dataset_id: str
    dataset_type: str
    segment_id: str
    resolved_entrypoint: Path
    working_dir_path: Path
    command: Tuple[str, ...]
    started_at: str
    finished_at: str
    duration_seconds: float
    status: str
    exit_code: Optional[int]
    stdout_path: Path
    stderr_path: Path
    output_source_path: Path
    output_result_path: Optional[Path]
    output_checks: Dict[str, Any]
    algorithm_failure: bool
    failure_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": RUN_RECEIPT_SCHEMA_VERSION,
            "test_id": self.test_id,
            "algorithm": self.algorithm_id,
            "contract_version": self.contract_version,
            "dataset_id": self.dataset_id,
            "dataset_type": self.dataset_type,
            "segment_id": self.segment_id,
            "resolved_entrypoint": str(self.resolved_entrypoint),
            "working_dir_path": str(self.working_dir_path),
            "command": list(self.command),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "output_source_path": str(self.output_source_path),
            "output_result_path": (
                None
                if self.output_result_path is None
                else str(self.output_result_path)
            ),
            "output_checks": dict(self.output_checks),
            "algorithm_failure": self.algorithm_failure,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class DatasetRunReceipt:
    test_id: str
    algorithm_id: str
    dataset_id: str
    dataset_type: str
    dataset_path: Path
    status: str
    successful_segment_ids: Tuple[str, ...]
    failed_segment_ids: Tuple[str, ...]
    not_run_segment_ids: Tuple[str, ...]
    algorithm_failure_count: int
    failure_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": RUN_RECEIPT_SCHEMA_VERSION,
            "test_id": self.test_id,
            "algorithm": self.algorithm_id,
            "dataset_id": self.dataset_id,
            "dataset_type": self.dataset_type,
            "dataset_path": str(self.dataset_path),
            "status": self.status,
            "successful_segment_ids": list(self.successful_segment_ids),
            "failed_segment_ids": list(self.failed_segment_ids),
            "not_run_segment_ids": list(self.not_run_segment_ids),
            "algorithm_failure_count": self.algorithm_failure_count,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class RunCheckpoint:
    test_id: str
    algorithm_id: str
    contract_version: int
    git_commit: str
    failure_policy: str
    failure_threshold: int
    timeout_seconds: float
    dataset_order: Tuple[str, ...]
    next_dataset_index: int
    finished_dataset_ids: Tuple[str, ...]
    dataset_receipt_paths: Tuple[str, ...]
    preflight_issues: Tuple[RunIssue, ...]
    algorithm_failure_count: int
    status: str
    updated_at: str
    failure_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": RUN_CHECKPOINT_SCHEMA_VERSION,
            "test_id": self.test_id,
            "algorithm": self.algorithm_id,
            "contract_version": self.contract_version,
            "git_commit": self.git_commit,
            "failure_policy": self.failure_policy,
            "failure_threshold": self.failure_threshold,
            "timeout_seconds": self.timeout_seconds,
            "dataset_order": list(self.dataset_order),
            "next_dataset_index": self.next_dataset_index,
            "finished_dataset_ids": list(self.finished_dataset_ids),
            "dataset_receipt_paths": list(self.dataset_receipt_paths),
            "preflight_issues": [item.to_dict() for item in self.preflight_issues],
            "algorithm_failure_count": self.algorithm_failure_count,
            "status": self.status,
            "updated_at": self.updated_at,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "RunCheckpoint":
        try:
            if int(value["schema_version"]) != RUN_CHECKPOINT_SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            raw_issues = value["preflight_issues"]
            if not isinstance(raw_issues, list):
                raise ValueError("preflight_issues must be a list")
            issues = []
            for item in raw_issues:
                if not isinstance(item, dict):
                    raise ValueError("preflight issue must be a mapping")
                dataset_id = item.get("dataset_id")
                issues.append(
                    RunIssue(
                        code=str(item["code"]),
                        path=Path(str(item["path"])).expanduser().resolve(),
                        message=str(item["message"]),
                        dataset_id=(None if dataset_id is None else str(dataset_id)),
                    )
                )
            failure_reason = value.get("failure_reason")
            return cls(
                test_id=str(value["test_id"]),
                algorithm_id=str(value["algorithm"]),
                contract_version=int(value["contract_version"]),
                git_commit=str(value["git_commit"]),
                failure_policy=str(value["failure_policy"]),
                failure_threshold=int(value["failure_threshold"]),
                timeout_seconds=float(value["timeout_seconds"]),
                dataset_order=tuple(str(item) for item in value["dataset_order"]),
                next_dataset_index=int(value["next_dataset_index"]),
                finished_dataset_ids=tuple(
                    str(item) for item in value["finished_dataset_ids"]
                ),
                dataset_receipt_paths=tuple(
                    str(item) for item in value["dataset_receipt_paths"]
                ),
                preflight_issues=tuple(issues),
                algorithm_failure_count=int(value["algorithm_failure_count"]),
                status=str(value["status"]),
                updated_at=str(value["updated_at"]),
                failure_reason=(
                    None if failure_reason is None else str(failure_reason)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid run checkpoint") from exc


@dataclass(frozen=True)
class RunSummary:
    status: str
    result_root: Path
    total_datasets: int
    successful_datasets: int
    failed_datasets: int
    not_run_datasets: int
    successful_segments: int
    failed_segments: int
    not_run_segments: int
    algorithm_failure_count: int
    failure_threshold: int
    failure_reason: Optional[str] = None
