"""Run one algorithm process and validate its contract-owned fixed output."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..algorithms.contracts import AlgorithmContract
from ..datasets.models import DatasetInstance, Segment
from .models import ProcessResult, ResolvedRunCommand

_PROCESS_TERMINATION_GRACE_SECONDS = 2.0
_MAX_MOCK_OUTPUT_BYTES = 10 * 1024 * 1024


class RunnerError(Exception):
    """The execution environment cannot safely start or record an algorithm."""


def resolve_fixed_output(
    algorithm_path: Path,
    relative_path: Path,
) -> Path:
    if relative_path.is_absolute():
        raise RunnerError(f"fixed output path must be relative: {relative_path}")
    root = Path(algorithm_path).expanduser().resolve()
    declared = root / relative_path
    if declared.is_symlink():
        raise RunnerError(f"fixed output must not be a symlink: {declared}")
    resolved = declared.resolve()
    if not _is_within(resolved, root):
        raise RunnerError(f"fixed output is outside algorithm path: {resolved}")
    return resolved


def prepare_fixed_output(path: Path) -> None:
    """Remove only the contract-declared generated output before one Segment."""

    if path.is_symlink():
        raise RunnerError(f"fixed output must not be a symlink: {path}")
    if not path.exists():
        return
    if not path.is_file():
        raise RunnerError(f"fixed output is not a regular file: {path}")
    try:
        path.unlink()
    except OSError as exc:
        raise RunnerError(f"cannot remove stale fixed output {path}: {exc}") from exc


def run_process(
    command: ResolvedRunCommand,
    working_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
) -> ProcessResult:
    if timeout_seconds <= 0:
        raise RunnerError("run timeout must be greater than zero")

    try:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RunnerError(f"cannot create run log directory: {exc}") from exc

    started_at = _utc_now()
    started_clock = time.monotonic()
    process: Optional[subprocess.Popen[bytes]] = None
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    list(command.argv),
                    cwd=working_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
            except OSError as exc:
                return _process_result(
                    "failed",
                    started_at,
                    started_clock,
                    None,
                    f"cannot start algorithm: {exc}",
                )

            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                _terminate_process_group(process)
                return _process_result(
                    "timeout",
                    started_at,
                    started_clock,
                    process.returncode,
                    f"algorithm exceeded timeout of {timeout_seconds:g} seconds",
                )
            except KeyboardInterrupt:
                _terminate_process_group(process)
                return _process_result(
                    "interrupted",
                    started_at,
                    started_clock,
                    process.returncode,
                    "algorithm interrupted by user",
                )
    except OSError as exc:
        raise RunnerError(f"cannot write run logs: {exc}") from exc

    if exit_code != 0:
        return _process_result(
            "failed",
            started_at,
            started_clock,
            exit_code,
            f"algorithm exited with code {exit_code}",
        )
    return _process_result(
        "success",
        started_at,
        started_clock,
        exit_code,
        None,
    )


def validate_fixed_output(
    path: Path,
    contract: AlgorithmContract,
    instance: DatasetInstance,
    segment: Segment,
    command: ResolvedRunCommand,
) -> Tuple[Dict[str, Any], Optional[str]]:
    checks: Dict[str, Any] = {
        "validator": contract.output_validator,
        "exists": path.exists(),
        "regular_file": False,
        "nonempty": False,
        "format_valid": False,
        "sha256": None,
    }
    if path.is_symlink():
        return checks, f"fixed output must not be a symlink: {path}"
    if not path.is_file():
        return checks, f"fixed output does not exist: {path}"

    checks["regular_file"] = True
    try:
        size = path.stat().st_size
    except OSError as exc:
        return checks, f"cannot inspect fixed output {path}: {exc}"
    checks["size_bytes"] = size
    if size <= 0:
        return checks, f"fixed output is empty: {path}"
    checks["nonempty"] = True

    if contract.output_validator == "mock_key_value":
        error = _validate_mock_key_value_output(
            path,
            contract,
            instance,
            segment,
            command,
        )
    else:
        error = f"unsupported output validator: {contract.output_validator}"
    if error is not None:
        return checks, error

    try:
        checks["sha256"] = _sha256_file(path)
    except OSError as exc:
        return checks, f"cannot hash fixed output {path}: {exc}"
    checks["format_valid"] = True
    return checks, None


def _validate_mock_key_value_output(
    path: Path,
    contract: AlgorithmContract,
    instance: DatasetInstance,
    segment: Segment,
    command: ResolvedRunCommand,
) -> Optional[str]:
    try:
        if path.stat().st_size > _MAX_MOCK_OUTPUT_BYTES:
            return f"mock output is unexpectedly large: {path}"
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"cannot read mock output {path}: {exc}"

    actual: Dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "=" not in line:
            return f"mock output line {line_number} is not key=value"
        key, value = line.split("=", 1)
        if not key or key in actual:
            return f"mock output has an invalid or duplicate key: {key!r}"
        actual[key] = value

    expected = {
        "algorithm": contract.algorithm_id,
        "dataset_type": instance.dataset_type,
        "dataset_root": str(instance.root_path.resolve()),
        "segment_start": command.argv[2],
        "segment_end": command.argv[3],
        **{f"input.{role}": value for role, value in command.input_arguments},
    }
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            key
            for key in set(actual).intersection(expected)
            if actual[key] != expected[key]
        )
        return (
            "mock output does not match run inputs "
            f"(missing={missing}, extra={extra}, changed={changed})"
        )

    try:
        if float(actual["segment_start"]) != segment.start_timestamp:
            return "mock output Segment start does not match"
        if float(actual["segment_end"]) != segment.end_timestamp:
            return "mock output Segment end does not match"
    except (KeyError, ValueError):
        return "mock output Segment timestamps are invalid"
    return None


def _process_result(
    status: str,
    started_at: str,
    started_clock: float,
    exit_code: Optional[int],
    failure_reason: Optional[str],
) -> ProcessResult:
    return ProcessResult(
        status=status,
        started_at=started_at,
        finished_at=_utc_now(),
        duration_seconds=round(time.monotonic() - started_clock, 9),
        exit_code=exit_code,
        failure_reason=failure_reason,
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
