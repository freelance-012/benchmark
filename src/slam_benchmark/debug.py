"""Concise module-level terminal tracing."""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import BinaryIO, Dict, Iterator, Mapping, Optional, Sequence, Tuple

_DEBUG_ENABLED: ContextVar[bool] = ContextVar(
    "slam_benchmark_debug_enabled",
    default=False,
)
_PRINT_LOCK = threading.Lock()
_VISIBLE_MODULES = {
    "DATASET_SCAN": "DATASET",
    "DATASET_LIST": "DATASET",
    "BUILD": "BUILD",
    "RUN": "RUN",
}


@contextmanager
def debug_mode(enabled: bool) -> Iterator[None]:
    """Enable tracing for one CLI invocation without leaking into later calls."""

    token = _DEBUG_ENABLED.set(bool(enabled))
    try:
        yield
    finally:
        _DEBUG_ENABLED.reset(token)


def debug_enabled() -> bool:
    return _DEBUG_ENABLED.get()


class DebugCommand:
    """One normalized command used by both tracing and execution."""

    def __init__(
        self,
        module: str,
        argv: Sequence[object],
        *,
        cwd: Optional[Path] = None,
    ):
        self.module = str(module).strip().upper()
        self.argv: Tuple[str, ...] = tuple(str(item) for item in argv)
        self.cwd = None if cwd is None else Path(cwd).expanduser().resolve()
        self._completed = False

    def __enter__(self) -> "DebugCommand":
        display = _display_module(self.module)
        if debug_enabled() and display is not None:
            values: Dict[str, object] = {}
            if self.cwd is not None:
                values["cwd"] = self.cwd
            values["command"] = shlex.join(_redacted_argv(self.argv))
            _emit_block(display, "INPUT", values)
        return self

    def complete(self, **values: object) -> None:
        if self._completed:
            return
        self._completed = True
        debug_output(self.module, **values)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del traceback
        if exc_type is not None and not self._completed:
            self.complete(status="failed", failure_reason=str(exc))
        return False


def debug_command(
    module: str,
    argv: Sequence[object],
    *,
    cwd: Optional[Path] = None,
) -> DebugCommand:
    return DebugCommand(module, argv, cwd=cwd)


def debug_output(module: str, **values: object) -> None:
    """Print one final module output after its files have been saved."""

    if not debug_enabled():
        return
    display = _display_module(module)
    if display is not None:
        _emit_block(display, "OUTPUT", values)


def process_output_targets(
    stdout: BinaryIO,
    stderr: BinaryIO,
) -> Tuple[object, object]:
    """Use pipes only in debug mode so output can be teed to logs and terminal."""

    if debug_enabled():
        return subprocess.PIPE, subprocess.PIPE
    return stdout, stderr


class DebugStreamSession:
    def __init__(self, threads: Sequence[threading.Thread], errors: list):
        self._threads = tuple(threads)
        self._errors = errors

    def finish(self) -> None:
        for thread in self._threads:
            thread.join()
        if self._errors:
            raise OSError(f"cannot stream process output: {self._errors[0]}")


def start_process_streams(
    process: "subprocess.Popen[bytes]",
    stdout_log: BinaryIO,
    stderr_log: BinaryIO,
    module: str,
) -> Optional[DebugStreamSession]:
    if not debug_enabled():
        return None
    if process.stdout is None or process.stderr is None:
        raise OSError("debug process pipes were not created")

    errors = []
    threads = (
        threading.Thread(
            target=_pump_process_stream,
            args=(process.stdout, stdout_log, module, "STDOUT", errors),
            daemon=True,
        ),
        threading.Thread(
            target=_pump_process_stream,
            args=(process.stderr, stderr_log, module, "STDERR", errors),
            daemon=True,
        ),
    )
    for thread in threads:
        thread.start()
    return DebugStreamSession(threads, errors)


def finish_process_streams(session: Optional[DebugStreamSession]) -> None:
    if session is not None:
        session.finish()


def _pump_process_stream(
    source: BinaryIO,
    destination: BinaryIO,
    module: str,
    stream_name: str,
    errors: list,
) -> None:
    pending = b""
    try:
        while True:
            chunk = source.read(4096)
            if not chunk:
                break
            destination.write(chunk)
            destination.flush()
            pending += chunk
            while b"\n" in pending:
                raw_line, pending = pending.split(b"\n", 1)
                _emit_stream_line(
                    module,
                    stream_name,
                    raw_line.decode("utf-8", errors="replace"),
                )
        if pending:
            _emit_stream_line(
                module,
                stream_name,
                pending.decode("utf-8", errors="replace"),
            )
    except BaseException as exc:  # pragma: no cover - defensive thread boundary
        errors.append(exc)
    finally:
        source.close()


def _display_module(module: str) -> Optional[str]:
    return _VISIBLE_MODULES.get(str(module).strip().upper())


def _emit_block(module: str, kind: str, values: Mapping[str, object]) -> None:
    visible = {key: value for key, value in values.items() if value is not None}
    lines = [f"[DEBUG][{module}][{kind}]"]
    for key, value in visible.items():
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            lines.extend(f"  - {_format_value(item)}" for item in value)
        else:
            lines.append(f"{key}: {_format_value(value)}")
    with _PRINT_LOCK:
        print("\n".join(lines), file=sys.stderr, flush=True)


def _emit_stream_line(
    module: str,
    stream_name: str,
    line: str,
) -> None:
    display = _display_module(module)
    if display is None:
        return
    prefix = f"[DEBUG][{display}][{stream_name.upper()}]"
    with _PRINT_LOCK:
        print(f"{prefix} {line}", file=sys.stderr, flush=True)


def _format_value(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _redacted_argv(argv: Sequence[str]) -> Tuple[str, ...]:
    sensitive_options = {
        "--password",
        "--token",
        "--access-token",
        "--secret",
        "--api-key",
    }
    redacted = []
    hide_next = False
    for item in argv:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        option = item.lower()
        if option in sensitive_options:
            redacted.append(item)
            hide_next = True
            continue
        if "=" in item and item.split("=", 1)[0].lower() in sensitive_options:
            redacted.append(f"{item.split('=', 1)[0]}=***")
            continue
        redacted.append(item)
    return tuple(redacted)
