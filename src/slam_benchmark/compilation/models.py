"""Data structures for algorithm compilation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

BUILD_RECEIPT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BuildConfig:
    algorithm_id: str
    algorithm_path: Path
    script_path: Path


@dataclass(frozen=True)
class GitSnapshot:
    commit: str
    branch: Optional[str]
    detached: bool
    dirty: bool
    tracked_changes: Tuple[str, ...]
    untracked_paths: Tuple[str, ...]
    tracked_state_digest: str
    submodules: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commit": self.commit,
            "branch": self.branch,
            "detached": self.detached,
            "dirty": self.dirty,
            "tracked_changes": list(self.tracked_changes),
            "untracked_paths": list(self.untracked_paths),
            "tracked_state_digest": self.tracked_state_digest,
            "submodules": list(self.submodules),
        }


@dataclass(frozen=True)
class BuildReceipt:
    algorithm_id: str
    contract_version: int
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: Optional[int]
    algorithm_path: Path
    script_path: Path
    script_digest: Optional[str]
    resolved_entrypoint: Optional[Path]
    stdout_path: Path
    stderr_path: Path
    git_before: Optional[GitSnapshot]
    git_after: Optional[GitSnapshot]
    failure_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": BUILD_RECEIPT_SCHEMA_VERSION,
            "algorithm": self.algorithm_id,
            "contract_version": self.contract_version,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "algorithm_path": str(self.algorithm_path),
            "script_path": str(self.script_path),
            "script_digest": self.script_digest,
            "resolved_entrypoint": (
                None
                if self.resolved_entrypoint is None
                else str(self.resolved_entrypoint)
            ),
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "git_before": (
                None if self.git_before is None else self.git_before.to_dict()
            ),
            "git_after": None if self.git_after is None else self.git_after.to_dict(),
            "failure_reason": self.failure_reason,
        }
