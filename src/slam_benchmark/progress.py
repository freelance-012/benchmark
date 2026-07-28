"""Terminal progress display for the benchmark pipeline."""

from __future__ import annotations

import math
import sys
import time
from contextlib import AbstractContextManager
from typing import IO, Dict, Iterable, Optional, Protocol, Type

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    Task,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Column
from rich.text import Text

MODULE_TOTAL = "total"
MODULE_DATASET = "dataset"
MODULE_BUILD = "build"
MODULE_RUN = "run"
MODULE_EVALUATE = "evaluate"
MODULE_REPORT = "report"

PIPELINE_MODULES = (
    MODULE_TOTAL,
    MODULE_DATASET,
    MODULE_BUILD,
    MODULE_RUN,
    MODULE_EVALUATE,
    MODULE_REPORT,
)

_MODULE_LABELS = {
    MODULE_TOTAL: "总进度",
    MODULE_DATASET: "DATASET",
    MODULE_BUILD: "BUILD",
    MODULE_RUN: "RUN",
    MODULE_EVALUATE: "EVALUATE",
    MODULE_REPORT: "REPORT",
}

_STATUS_LABELS = {
    "waiting": "等待",
    "running": "运行",
    "success": "完成",
    "warning": "有异常",
    "failed": "失败",
    "interrupted": "已中断",
    "skipped": "跳过",
}


class ProgressReporter(Protocol):
    """Small business-layer contract implemented by terminal and test reporters."""

    def prepare(
        self,
        module: str,
        *,
        total: int,
        completed: int = 0,
        detail: str = "",
    ) -> None: ...

    def begin(
        self,
        module: str,
        *,
        total: Optional[int] = None,
        completed: int = 0,
        detail: str = "",
    ) -> None: ...

    def describe(self, module: str, detail: str) -> None: ...

    def estimate(
        self,
        module: str,
        *,
        eta_seconds: float,
        detail: str = "",
    ) -> None: ...

    def advance(self, module: str, *, amount: int = 1, detail: str = "") -> None: ...

    def finish(
        self,
        module: str,
        *,
        status: str,
        detail: str = "",
        complete: bool = False,
    ) -> None: ...

    def close(self) -> None: ...


class NullProgressReporter:
    """No-op reporter used by API callers and tests that do not need a terminal."""

    def prepare(
        self,
        module: str,
        *,
        total: int,
        completed: int = 0,
        detail: str = "",
    ) -> None:
        del module, total, completed, detail

    def begin(
        self,
        module: str,
        *,
        total: Optional[int] = None,
        completed: int = 0,
        detail: str = "",
    ) -> None:
        del module, total, completed, detail

    def describe(self, module: str, detail: str) -> None:
        del module, detail

    def estimate(
        self,
        module: str,
        *,
        eta_seconds: float,
        detail: str = "",
    ) -> None:
        del module, eta_seconds, detail

    def advance(self, module: str, *, amount: int = 1, detail: str = "") -> None:
        del module, amount, detail

    def finish(
        self,
        module: str,
        *,
        status: str,
        detail: str = "",
        complete: bool = False,
    ) -> None:
        del module, status, detail, complete

    def close(self) -> None:
        return None


class _RemainingTimeColumn(ProgressColumn):
    """Show estimated remaining time only while a module can still make progress."""

    def render(self, task: Task) -> Text:
        state = str(task.fields.get("state", "waiting"))
        if task.finished or state in {
            "success",
            "warning",
            "failed",
            "interrupted",
            "skipped",
        }:
            return Text("")
        explicit = task.fields.get("eta_seconds")
        recorded_at = task.fields.get("eta_recorded_at")
        if isinstance(explicit, (int, float)) and math.isfinite(explicit):
            elapsed = (
                max(0.0, time.monotonic() - recorded_at)
                if isinstance(recorded_at, (int, float))
                else 0.0
            )
            remaining = max(0.0, float(explicit) - elapsed)
        else:
            remaining = task.time_remaining
        if remaining is None:
            return Text("预计剩余 --:--:--", style="dim")
        total_seconds = max(0, int(math.ceil(remaining)))
        hours, remainder = divmod(total_seconds, 60 * 60)
        minutes, seconds = divmod(remainder, 60)
        value = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return Text(f"预计剩余 {value}")


class _StateColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        state = str(task.fields.get("state", "waiting"))
        detail = str(task.fields.get("detail", ""))
        label = _STATUS_LABELS.get(state, state)
        text = label if not detail else f"{label}  {detail}"
        style = {
            "success": "green",
            "warning": "yellow",
            "failed": "red",
            "interrupted": "yellow",
            "skipped": "dim",
            "running": "cyan",
        }.get(state, "dim")
        return Text(text, style=style, overflow="ellipsis", no_wrap=True)


class TerminalProgress(AbstractContextManager["TerminalProgress"]):
    """One live terminal table containing overall and per-module progress bars."""

    def __init__(
        self,
        modules: Iterable[str] = PIPELINE_MODULES,
        *,
        enabled: Optional[bool] = None,
        output: Optional[IO[str]] = None,
        force_terminal: Optional[bool] = None,
        auto_refresh: bool = True,
    ):
        normalized = tuple(str(item).strip().lower() for item in modules)
        unknown = tuple(item for item in normalized if item not in _MODULE_LABELS)
        if unknown:
            raise ValueError(f"unknown progress module: {unknown[0]}")
        if len(set(normalized)) != len(normalized):
            raise ValueError("progress modules must be unique")

        stream = output or sys.stderr
        console = Console(
            file=stream,
            force_terminal=force_terminal,
            stderr=output is None,
        )
        if enabled is None:
            enabled = console.is_terminal
        self.enabled = bool(enabled)
        self.modules = normalized
        self._task_ids: Dict[str, int] = {}
        self._started = set()
        self._finished = set()
        self._closed = False
        self._progress = Progress(
            TextColumn(
                "{task.description}",
                table_column=Column(width=10, no_wrap=True),
            ),
            BarColumn(bar_width=22),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            _RemainingTimeColumn(),
            _StateColumn(table_column=Column(ratio=1, overflow="ellipsis")),
            console=console,
            auto_refresh=auto_refresh,
            refresh_per_second=4,
            transient=False,
            disable=not self.enabled,
            expand=True,
        )

    def __enter__(self) -> "TerminalProgress":
        if not self.enabled:
            return self
        self._progress.start()
        for module in self.modules:
            self._task_ids[module] = self._progress.add_task(
                _MODULE_LABELS[module],
                total=1,
                completed=0,
                start=False,
                state="waiting",
                detail="",
                eta_seconds=None,
                eta_recorded_at=None,
            )
        self._progress.refresh()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: object,
    ) -> Optional[bool]:
        del exc, traceback
        terminal_status = (
            "interrupted"
            if exc_type is not None and issubclass(exc_type, KeyboardInterrupt)
            else "failed"
        )
        self._close(terminal_status)
        return None

    def prepare(
        self,
        module: str,
        *,
        total: int,
        completed: int = 0,
        detail: str = "",
    ) -> None:
        if not self.enabled:
            return
        task_id = self._task_id(module)
        self._progress.update(
            task_id,
            total=max(0, total),
            completed=max(0, completed),
            state="waiting",
            detail=detail,
            eta_seconds=None,
            eta_recorded_at=None,
        )

    def begin(
        self,
        module: str,
        *,
        total: Optional[int] = None,
        completed: int = 0,
        detail: str = "",
    ) -> None:
        if not self.enabled:
            return
        task_id = self._task_id(module)
        self._progress.update(
            task_id,
            total=None if total is None else max(0, total),
            completed=max(0, completed),
            state="running",
            detail=detail,
            eta_seconds=None,
            eta_recorded_at=None,
        )
        if module not in self._started:
            self._progress.start_task(task_id)
            self._started.add(module)
        self._finished.discard(module)

    def describe(self, module: str, detail: str) -> None:
        if not self.enabled:
            return
        self._progress.update(
            self._task_id(module),
            state="running",
            detail=detail,
        )

    def estimate(
        self,
        module: str,
        *,
        eta_seconds: float,
        detail: str = "",
    ) -> None:
        if not self.enabled:
            return
        if not math.isfinite(eta_seconds) or eta_seconds < 0:
            return
        values = {
            "state": "running",
            "eta_seconds": float(eta_seconds),
            "eta_recorded_at": time.monotonic(),
        }
        if detail:
            values["detail"] = detail
        self._progress.update(self._task_id(module), **values)

    def advance(self, module: str, *, amount: int = 1, detail: str = "") -> None:
        if not self.enabled:
            return
        task_id = self._task_id(module)
        if module not in self._started:
            self._progress.start_task(task_id)
            self._started.add(module)
        self._progress.update(
            task_id,
            advance=max(0, amount),
            state="running",
            detail=detail,
        )

    def finish(
        self,
        module: str,
        *,
        status: str,
        detail: str = "",
        complete: bool = False,
    ) -> None:
        if not self.enabled:
            return
        if status not in _STATUS_LABELS:
            raise ValueError(f"unknown progress status: {status}")
        task_id = self._task_id(module)
        task = self._progress.tasks[task_id]
        values = {
            "state": status,
            "detail": detail or str(task.fields.get("detail", "")),
            "eta_seconds": 0.0,
            "eta_recorded_at": time.monotonic(),
        }
        if complete:
            total = task.total
            if total is None or total <= 0:
                values["total"] = 1
                values["completed"] = 1
            else:
                values["completed"] = total
        self._progress.update(task_id, **values)
        if module not in self._started:
            self._progress.start_task(task_id)
            self._started.add(module)
        self._progress.stop_task(task_id)
        self._finished.add(module)

    def refresh(self) -> None:
        if self.enabled:
            self._progress.refresh()

    def close(self) -> None:
        self._close("failed")

    def _close(self, unfinished_status: str) -> None:
        if not self.enabled or self._closed:
            return
        for module in self._started - self._finished:
            self.finish(module, status=unfinished_status)
        self._progress.stop()
        self._closed = True

    def _task_id(self, module: str) -> int:
        normalized = str(module).strip().lower()
        try:
            return self._task_ids[normalized]
        except KeyError as exc:
            raise ValueError(f"progress module is not displayed: {normalized}") from exc
