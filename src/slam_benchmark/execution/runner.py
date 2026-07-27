"""Run one algorithm process and validate its contract-owned fixed output."""

from __future__ import annotations

import hashlib
import math
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..algorithms.contracts import AlgorithmContract
from ..datasets.models import DatasetInstance, Segment
from ..debug import (
    debug_command,
    finish_process_streams,
    process_output_targets,
    start_process_streams,
)
from .models import ProcessResult, ResolvedRunCommand

_PROCESS_TERMINATION_GRACE_SECONDS = 2.0
_MAX_MOCK_OUTPUT_BYTES = 10 * 1024 * 1024
_MOCK_TIMESTAMP_TOLERANCE_SECONDS = 1e-3


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

    with debug_command(
        "RUN",
        command.argv,
        cwd=working_dir,
    ) as trace:
        return _execute_process(
            trace.argv,
            working_dir,
            stdout_path,
            stderr_path,
            timeout_seconds,
        )


def _execute_process(
    argv: Tuple[str, ...],
    working_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
) -> ProcessResult:
    try:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RunnerError(f"cannot create run log directory: {exc}") from exc

    started_at = _utc_now()
    started_clock = time.monotonic()
    process: Optional[subprocess.Popen[bytes]] = None
    streams = None
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                stdout_target, stderr_target = process_output_targets(stdout, stderr)
                process = subprocess.Popen(
                    list(argv),
                    cwd=working_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_target,
                    stderr=stderr_target,
                    start_new_session=True,
                )
                streams = start_process_streams(
                    process,
                    stdout,
                    stderr,
                    "RUN",
                )
            except OSError as exc:
                if process is not None:
                    _terminate_process_group(process)
                return _process_result(
                    "failed",
                    started_at,
                    started_clock,
                    None if process is None else process.returncode,
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
            finally:
                finish_process_streams(streams)
    except KeyboardInterrupt:
        if process is not None:
            _terminate_process_group(process)
        return _process_result(
            "interrupted",
            started_at,
            started_clock,
            None if process is None else process.returncode,
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
    elif contract.output_validator == "sf_vo":
        output_details, error = _validate_sf_vo_output(path, segment)
        checks.update(output_details)
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


def validate_additional_output(
    path: Path,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Validate a contract-declared sidecar output without interpreting it."""

    checks: Dict[str, Any] = {
        "validator": "regular_nonempty_file",
        "exists": path.exists(),
        "regular_file": False,
        "nonempty": False,
        "format_valid": False,
        "sha256": None,
    }
    if path.is_symlink():
        return checks, f"generated output must not be a symlink: {path}"
    if not path.is_file():
        return checks, f"generated output does not exist: {path}"

    checks["regular_file"] = True
    try:
        size = path.stat().st_size
    except OSError as exc:
        return checks, f"cannot inspect generated output {path}: {exc}"
    checks["size_bytes"] = size
    if size <= 0:
        return checks, f"generated output is empty: {path}"
    checks["nonempty"] = True

    try:
        checks["sha256"] = _sha256_file(path)
    except OSError as exc:
        return checks, f"cannot hash generated output {path}: {exc}"
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
        **{f"input.{role}": value for role, value in command.input_arguments},
    }
    expected_keys = set(expected).union({"segment_start", "segment_end"})
    if set(actual) != expected_keys or any(
        actual.get(key) != value for key, value in expected.items()
    ):
        missing = sorted(expected_keys - set(actual))
        extra = sorted(set(actual) - expected_keys)
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
        if not math.isclose(
            float(actual["segment_start"]),
            segment.start_timestamp,
            rel_tol=0.0,
            abs_tol=_MOCK_TIMESTAMP_TOLERANCE_SECONDS,
        ):
            return "mock output Segment start does not match"
        if not math.isclose(
            float(actual["segment_end"]),
            segment.end_timestamp,
            rel_tol=0.0,
            abs_tol=_MOCK_TIMESTAMP_TOLERANCE_SECONDS,
        ):
            return "mock output Segment end does not match"
    except (KeyError, ValueError):
        return "mock output Segment timestamps are invalid"
    return None


def _validate_sf_vo_output(
    path: Path,
    segment: Segment,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Validate the fixed 11-column format consumed by voeval sf_vo."""

    details: Dict[str, Any] = {"row_count": 0}
    previous_timestamp: Optional[float] = None
    tolerance = max(
        1e-6,
        abs(segment.start_timestamp) * 1e-9,
        abs(segment.end_timestamp) * 1e-9,
    )
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                fields = line.replace(",", " ").replace(";", " ").split()
                if len(fields) != 11:
                    return details, (
                        f"{path}:{line_number}: sf_vo expects exactly 11 columns; "
                        f"got {len(fields)}"
                    )
                try:
                    values = [float(field) for field in fields]
                except ValueError:
                    return details, (
                        f"{path}:{line_number}: sf_vo contains a non-numeric value"
                    )
                if not all(math.isfinite(value) for value in values):
                    return details, (
                        f"{path}:{line_number}: sf_vo contains NaN or infinity"
                    )

                timestamp = values[0]
                if timestamp < segment.start_timestamp - tolerance:
                    return details, (
                        f"{path}:{line_number}: sf_vo timestamp precedes Segment start"
                    )
                if timestamp > segment.end_timestamp + tolerance:
                    return details, (
                        f"{path}:{line_number}: sf_vo timestamp exceeds Segment end"
                    )
                if previous_timestamp is not None and timestamp <= previous_timestamp:
                    return details, (
                        f"{path}:{line_number}: sf_vo timestamps must be strictly increasing"
                    )
                if values[1] < 0:
                    return details, (
                        f"{path}:{line_number}: sf_vo num_inliers must not be negative"
                    )
                if values[9] < 0:
                    return details, (
                        f"{path}:{line_number}: sf_vo time_cost must not be negative"
                    )
                if not _is_integer(values[8]) or int(round(values[8])) not in (0, 1):
                    return details, (
                        f"{path}:{line_number}: sf_vo is_keyframe must be 0 or 1"
                    )
                if not _is_integer(values[10]) or values[10] < 0:
                    return details, (
                        f"{path}:{line_number}: sf_vo reset_count must be a non-negative integer"
                    )

                if details["row_count"] == 0:
                    details["first_timestamp"] = timestamp
                details["last_timestamp"] = timestamp
                details["row_count"] += 1
                previous_timestamp = timestamp
    except (OSError, UnicodeDecodeError) as exc:
        return details, f"cannot read sf_vo output {path}: {exc}"

    if details["row_count"] < 2:
        return details, f"{path}: sf_vo output contains fewer than two poses"
    return details, None


def _is_integer(value: float) -> bool:
    return math.isfinite(value) and abs(value - round(value)) <= 1e-9


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
