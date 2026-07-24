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

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "GitSnapshot":
        try:
            branch_value = value.get("branch")
            detached = value["detached"]
            dirty = value["dirty"]
            if not isinstance(detached, bool) or not isinstance(dirty, bool):
                raise ValueError("detached and dirty must be booleans")
            return cls(
                commit=str(value["commit"]),
                branch=None if branch_value is None else str(branch_value),
                detached=detached,
                dirty=dirty,
                tracked_changes=tuple(str(item) for item in value["tracked_changes"]),
                untracked_paths=tuple(str(item) for item in value["untracked_paths"]),
                tracked_state_digest=str(value["tracked_state_digest"]),
                submodules=tuple(str(item) for item in value["submodules"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid Git snapshot") from exc


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

    @property
    def result_root(self) -> Path:
        return self.stdout_path.parent.parent

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

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "BuildReceipt":
        try:
            if int(value["schema_version"]) != BUILD_RECEIPT_SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            git_before_value = value.get("git_before")
            git_after_value = value.get("git_after")
            if git_before_value is not None and not isinstance(git_before_value, dict):
                raise ValueError("git_before must be a mapping")
            if git_after_value is not None and not isinstance(git_after_value, dict):
                raise ValueError("git_after must be a mapping")
            resolved_entrypoint = value.get("resolved_entrypoint")
            exit_code = value.get("exit_code")
            script_digest = value.get("script_digest")
            failure_reason = value.get("failure_reason")
            return cls(
                algorithm_id=str(value["algorithm"]),
                contract_version=int(value["contract_version"]),
                status=str(value["status"]),
                started_at=str(value["started_at"]),
                finished_at=str(value["finished_at"]),
                duration_seconds=float(value["duration_seconds"]),
                exit_code=None if exit_code is None else int(exit_code),
                algorithm_path=Path(str(value["algorithm_path"]))
                .expanduser()
                .resolve(),
                script_path=Path(str(value["script_path"])).expanduser().resolve(),
                script_digest=(None if script_digest is None else str(script_digest)),
                resolved_entrypoint=(
                    None
                    if resolved_entrypoint is None
                    else Path(str(resolved_entrypoint)).expanduser().resolve()
                ),
                stdout_path=Path(str(value["stdout_path"])).expanduser().resolve(),
                stderr_path=Path(str(value["stderr_path"])).expanduser().resolve(),
                git_before=(
                    None
                    if git_before_value is None
                    else GitSnapshot.from_dict(git_before_value)
                ),
                git_after=(
                    None
                    if git_after_value is None
                    else GitSnapshot.from_dict(git_after_value)
                ),
                failure_reason=(
                    None if failure_reason is None else str(failure_reason)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid build receipt") from exc
