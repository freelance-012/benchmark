"""Data structures shared by dataset scanning, storage, and CLI output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DatasetScanConfig:
    root_path: Path
    dataset_type: str


@dataclass(frozen=True)
class Segment:
    segment_id: str
    sequence_no: int
    start_timestamp: float
    end_timestamp: float
    duration_seconds: float
    frame_count: int
    valid: bool
    invalid_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.segment_id,
            "sequence_no": self.sequence_no,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "duration_seconds": self.duration_seconds,
            "frame_count": self.frame_count,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Segment":
        try:
            valid = value["valid"]
            if not isinstance(valid, bool):
                raise ValueError("valid must be a boolean")
            return cls(
                segment_id=str(value["id"]),
                sequence_no=int(value["sequence_no"]),
                start_timestamp=float(value["start_timestamp"]),
                end_timestamp=float(value["end_timestamp"]),
                duration_seconds=float(value["duration_seconds"]),
                frame_count=int(value["frame_count"]),
                valid=valid,
                invalid_reason=(
                    None
                    if value.get("invalid_reason") is None
                    else str(value["invalid_reason"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid segment record") from exc


@dataclass(frozen=True)
class DatasetInstance:
    dataset_id: str
    dataset_type: str
    root_path: Path
    handler_version: int
    input_paths: Dict[str, Optional[str]]
    segments: Tuple[Segment, ...]

    @property
    def valid_segment_count(self) -> int:
        return sum(segment.valid for segment in self.segments)

    @property
    def status(self) -> str:
        return "ready" if self.valid_segment_count else "unavailable"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "dataset": {
                "id": self.dataset_id,
                "type": self.dataset_type,
                "root_path": str(self.root_path),
                "handler_version": self.handler_version,
            },
            "inputs": dict(self.input_paths),
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "DatasetInstance":
        try:
            if int(value["schema_version"]) != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            dataset = value["dataset"]
            inputs = value["inputs"]
            segments = value["segments"]
            if not isinstance(dataset, dict) or not isinstance(inputs, dict):
                raise ValueError("dataset and inputs must be mappings")
            if not isinstance(segments, list):
                raise ValueError("segments must be a list")
            input_paths = {
                str(key): None if item is None else str(item)
                for key, item in inputs.items()
            }
            return cls(
                dataset_id=str(dataset["id"]),
                dataset_type=str(dataset["type"]).lower(),
                root_path=Path(str(dataset["root_path"])).expanduser().resolve(),
                handler_version=int(dataset["handler_version"]),
                input_paths=input_paths,
                segments=tuple(Segment.from_dict(item) for item in segments),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid dataset instance") from exc


@dataclass(frozen=True)
class ScanDiagnostic:
    level: str
    code: str
    path: Path
    message: str


@dataclass(frozen=True)
class ScanReport:
    datasets: Tuple[DatasetInstance, ...]
    diagnostics: Tuple[ScanDiagnostic, ...]

    @property
    def has_errors(self) -> bool:
        return any(item.level == "error" for item in self.diagnostics)
