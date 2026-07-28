"""Data structures for one voeval invocation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

EVALUATION_RECEIPT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EvaluationRequest:
    test_id: str
    algorithm_id: str
    run_index: int
    dataset_id: str
    dataset_type: str
    segment_id: str
    workflow: str
    data_dir: Path
    log_dir: Path
    evaluation_dir: Path


@dataclass(frozen=True)
class EvaluationReceipt:
    test_id: str
    algorithm_id: str
    run_index: int
    dataset_id: str
    dataset_type: str
    segment_id: str
    workflow: str
    command: Tuple[str, ...]
    started_at: str
    finished_at: str
    duration_seconds: float
    status: str
    exit_code: Optional[int]
    metrics_path: Path
    log_path: Path
    invalid_metrics: Tuple[str, ...]
    failure_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": EVALUATION_RECEIPT_SCHEMA_VERSION,
            "test_id": self.test_id,
            "algorithm": self.algorithm_id,
            "run_index": self.run_index,
            "dataset_id": self.dataset_id,
            "dataset_type": self.dataset_type,
            "segment_id": self.segment_id,
            "workflow": self.workflow,
            "command": list(self.command),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "exit_code": self.exit_code,
            "metrics_path": str(self.metrics_path),
            "log_path": str(self.log_path),
            "invalid_metrics": list(self.invalid_metrics),
            "failure_reason": self.failure_reason,
        }
