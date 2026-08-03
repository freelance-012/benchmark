"""Data structures for one voeval invocation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

EVALUATION_RECEIPT_SCHEMA_VERSION = 1
DEFAULT_RPE_DELTA_VALUE = 100.0
DEFAULT_RPE_DELTA_UNIT = "m"
RPE_DELTA_UNITS = ("m", "f")


def normalize_rpe_delta(value: float, unit: str) -> Tuple[float, str]:
    try:
        normalized_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("RPE delta must be a number") from exc
    if not math.isfinite(normalized_value) or normalized_value <= 0:
        raise ValueError("RPE delta must be a positive finite number")

    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in RPE_DELTA_UNITS:
        choices = ", ".join(RPE_DELTA_UNITS)
        raise ValueError(f"RPE delta unit must be one of: {choices}")
    if normalized_unit == "f" and not normalized_value.is_integer():
        raise ValueError("RPE frame delta must be a positive integer")
    return normalized_value, normalized_unit


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
    rpe_delta_value: float = DEFAULT_RPE_DELTA_VALUE
    rpe_delta_unit: str = DEFAULT_RPE_DELTA_UNIT
    vo_filename: Optional[str] = None


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
    vo_filename: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
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
        if self.vo_filename is not None:
            result["vo_filename"] = self.vo_filename
        return result
