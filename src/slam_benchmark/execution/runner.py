"""Run one algorithm process and resolve its contract-owned fixed output."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ..debug import (
    OutputLineCallback,
    debug_command,
    finish_process_streams,
    process_output_targets,
    start_process_streams,
)
from .models import ProcessResult, ResolvedRunCommand

_PROCESS_TERMINATION_GRACE_SECONDS = 2.0


class RunnerError(Exception):
    """The execution environment cannot safely start or record an algorithm."""


@dataclass(frozen=True)
class NumberedOutputSnapshot:
    """One stable view of an algorithm-owned numbered output root."""

    root_path: Path
    counter_path: Path
    last_completed: int


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


def read_numbered_output_snapshot(
    output_root_path: Path,
    counter_relative_path: Path,
    *,
    allow_uninitialized: bool,
) -> NumberedOutputSnapshot:
    """Read an algorithm-owned counter without guessing the newest directory."""

    root = Path(output_root_path).expanduser().resolve()
    if counter_relative_path.is_absolute() or ".." in counter_relative_path.parts:
        raise RunnerError(
            f"numbered output counter path must be relative: {counter_relative_path}"
        )
    counter_declared = root / counter_relative_path
    if counter_declared.is_symlink():
        raise RunnerError(
            f"numbered output counter must not be a symlink: {counter_declared}"
        )
    counter = counter_declared.resolve()
    if not _is_within(counter, root):
        raise RunnerError(f"numbered output counter escapes output root: {counter}")

    if not root.exists():
        if allow_uninitialized:
            return NumberedOutputSnapshot(root, counter, -1)
        raise RunnerError(f"numbered output root does not exist: {root}")
    if not root.is_dir():
        raise RunnerError(f"numbered output root is not a directory: {root}")

    if not counter.exists():
        if allow_uninitialized:
            try:
                has_existing_content = next(root.iterdir(), None) is not None
            except OSError as exc:
                raise RunnerError(
                    f"cannot inspect numbered output root {root}: {exc}"
                ) from exc
            if not has_existing_content:
                return NumberedOutputSnapshot(root, counter, -1)
        raise RunnerError(f"numbered output counter does not exist: {counter}")
    if not counter.is_file():
        raise RunnerError(f"numbered output counter is not a file: {counter}")

    try:
        content = counter.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RunnerError(
            f"cannot read numbered output counter {counter}: {exc}"
        ) from exc
    number_text = content[:-1] if content.endswith("\n") else content
    if not number_text or number_text.strip() != number_text or "\n" in number_text:
        raise RunnerError(
            f"numbered output counter must contain only one integer: {counter}"
        )
    try:
        last_completed = int(number_text, 10)
    except ValueError as exc:
        raise RunnerError(
            f"numbered output counter must contain only one integer: {counter}"
        ) from exc
    if last_completed < 0 or number_text != str(last_completed):
        raise RunnerError(
            "numbered output counter must contain one non-negative canonical "
            f"integer: {counter}"
        )
    return NumberedOutputSnapshot(root, counter, last_completed)


def resolve_numbered_output_sources(
    before: NumberedOutputSnapshot,
    counter_relative_path: Path,
    output_relative_paths: Sequence[Path],
) -> Tuple[NumberedOutputSnapshot, Path, Tuple[Path, ...]]:
    """Locate exactly one new numbered output produced by the last process."""

    after = read_numbered_output_snapshot(
        before.root_path,
        counter_relative_path,
        allow_uninitialized=False,
    )
    expected = before.last_completed + 1
    if after.last_completed != expected:
        raise RunnerError(
            "numbered output counter did not advance exactly once: "
            f"before={before.last_completed}, after={after.last_completed}"
        )

    directory_declared = after.root_path / str(after.last_completed)
    if directory_declared.is_symlink():
        raise RunnerError(
            f"numbered output directory must not be a symlink: {directory_declared}"
        )
    output_directory = directory_declared.resolve()
    if not _is_within(output_directory, after.root_path):
        raise RunnerError(
            f"numbered output directory escapes output root: {output_directory}"
        )
    if not output_directory.is_dir():
        raise RunnerError(
            f"numbered output directory does not exist: {output_directory}"
        )

    output_sources = []
    for relative_path in output_relative_paths:
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RunnerError(f"fixed output path must be relative: {relative_path}")
        declared = output_directory / relative_path
        if declared.is_symlink():
            raise RunnerError(f"fixed output must not be a symlink: {declared}")
        resolved = declared.resolve()
        if not _is_within(resolved, output_directory):
            raise RunnerError(
                f"fixed output escapes numbered output directory: {resolved}"
            )
        output_sources.append(resolved)
    return after, output_directory, tuple(output_sources)


def run_process(
    command: ResolvedRunCommand,
    working_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
    output_line_callback: Optional[OutputLineCallback] = None,
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
            output_line_callback,
        )


def _execute_process(
    argv: Tuple[str, ...],
    working_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
    output_line_callback: Optional[OutputLineCallback],
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
                stdout_target, stderr_target = process_output_targets(
                    stdout,
                    stderr,
                    stream_output=output_line_callback is not None,
                )
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
                    output_line_callback,
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


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
