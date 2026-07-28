"""Parse algorithm progress output and estimate remaining Segment runtime."""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence

from ..datasets.models import Segment

PROGRESS_PARSER_BENCHMARK_JSON_V1 = "benchmark_json_v1"
_PROGRESS_PREFIX = "BENCHMARK_PROGRESS "
_INITIAL_DATA_RATE = 1.0
_LIVE_RATE_WEIGHT = 0.7
_COMPLETED_RATE_WEIGHT = 0.5


@dataclass(frozen=True)
class AlgorithmProgress:
    """One normalized progress sample emitted while an algorithm is running."""

    timestamp: Optional[float] = None
    frame: Optional[float] = None
    fps: Optional[float] = None
    percent: Optional[float] = None


@dataclass(frozen=True)
class RuntimeEstimate:
    """Current Segment progress plus the predicted remaining RUN time."""

    fraction: float
    eta_seconds: float
    data_rate: float
    source: str


ProgressParser = Callable[[str], Optional[AlgorithmProgress]]


def progress_parser(parser_id: Optional[str]) -> Optional[ProgressParser]:
    """Resolve one framework-owned parser selected by an algorithm contract."""

    if parser_id is None:
        return None
    parsers: Dict[str, ProgressParser] = {
        PROGRESS_PARSER_BENCHMARK_JSON_V1: _parse_benchmark_json_v1,
    }
    try:
        return parsers[parser_id]
    except KeyError as exc:
        raise ValueError(f"unsupported algorithm progress parser: {parser_id}") from exc


class RunEtaEstimator:
    """Estimate RUN time from Segment duration and live algorithm progress."""

    def __init__(
        self,
        segments: Sequence[Segment],
        *,
        initial_data_rate: float = _INITIAL_DATA_RATE,
    ):
        if not math.isfinite(initial_data_rate) or initial_data_rate <= 0:
            raise ValueError("initial data rate must be greater than zero")
        self._segments = tuple(segments)
        self._data_rate = float(initial_data_rate)
        self._lock = threading.Lock()
        self._current_index: Optional[int] = None
        self._current_started = 0.0
        self._last_observed_at = 0.0
        self._last_fraction = 0.0
        self._integrated_frames = 0.0

    def start_segment(
        self,
        run_index: int,
        *,
        now: Optional[float] = None,
    ) -> RuntimeEstimate:
        with self._lock:
            if run_index < 0 or run_index >= len(self._segments):
                raise ValueError(f"run index is outside Segment plan: {run_index}")
            current = time.monotonic() if now is None else float(now)
            self._current_index = run_index
            self._current_started = current
            self._last_observed_at = current
            self._last_fraction = 0.0
            self._integrated_frames = 0.0
            return self._estimate(0.0, "duration")

    def observe(
        self,
        sample: AlgorithmProgress,
        *,
        now: Optional[float] = None,
    ) -> Optional[RuntimeEstimate]:
        with self._lock:
            if self._current_index is None:
                return None
            current = time.monotonic() if now is None else float(now)
            segment = self._segments[self._current_index]
            fraction, source = self._fraction(sample, segment, current)
            if fraction is None:
                self._last_observed_at = current
                return None
            fraction = min(1.0, max(self._last_fraction, fraction))
            if fraction <= self._last_fraction:
                self._last_observed_at = current
                return None

            elapsed = max(0.0, current - self._current_started)
            processed_duration = segment.duration_seconds * fraction
            if elapsed > 0 and processed_duration > 0:
                observed_rate = processed_duration / elapsed
                if math.isfinite(observed_rate) and observed_rate > 0:
                    self._data_rate = (
                        _LIVE_RATE_WEIGHT * observed_rate
                        + (1.0 - _LIVE_RATE_WEIGHT) * self._data_rate
                    )
            self._last_fraction = fraction
            self._last_observed_at = current
            return self._estimate(fraction, source)

    def finish_segment(
        self,
        *,
        successful: bool,
        duration_seconds: float,
    ) -> RuntimeEstimate:
        with self._lock:
            if self._current_index is None:
                return RuntimeEstimate(1.0, 0.0, self._data_rate, "completed")
            segment = self._segments[self._current_index]
            if successful and duration_seconds > 0 and segment.duration_seconds > 0:
                completed_rate = segment.duration_seconds / duration_seconds
                if math.isfinite(completed_rate) and completed_rate > 0:
                    self._data_rate = (
                        _COMPLETED_RATE_WEIGHT * completed_rate
                        + (1.0 - _COMPLETED_RATE_WEIGHT) * self._data_rate
                    )
            completed_index = self._current_index
            self._current_index = None
            remaining_duration = sum(
                max(0.0, item.duration_seconds)
                for item in self._segments[completed_index + 1 :]
            )
            return RuntimeEstimate(
                fraction=1.0,
                eta_seconds=remaining_duration / self._data_rate,
                data_rate=self._data_rate,
                source="completed",
            )

    def _fraction(
        self,
        sample: AlgorithmProgress,
        segment: Segment,
        current: float,
    ) -> tuple:
        if sample.percent is not None and 0 <= sample.percent <= 100:
            return sample.percent / 100.0, "percent"

        timestamp_span = segment.end_timestamp - segment.start_timestamp
        if sample.timestamp is not None and timestamp_span > 0:
            return (
                (sample.timestamp - segment.start_timestamp) / timestamp_span,
                "timestamp",
            )

        if sample.frame is not None and segment.frame_count > 0:
            return sample.frame / segment.frame_count, "frame"

        if sample.fps is not None and sample.fps > 0 and segment.frame_count > 0:
            elapsed = max(0.0, current - self._last_observed_at)
            self._integrated_frames += sample.fps * elapsed
            return self._integrated_frames / segment.frame_count, "fps"
        return None, "unknown"

    def _estimate(self, fraction: float, source: str) -> RuntimeEstimate:
        assert self._current_index is not None
        segment = self._segments[self._current_index]
        remaining_duration = max(0.0, segment.duration_seconds) * (1.0 - fraction)
        remaining_duration += sum(
            max(0.0, item.duration_seconds)
            for item in self._segments[self._current_index + 1 :]
        )
        return RuntimeEstimate(
            fraction=fraction,
            eta_seconds=remaining_duration / self._data_rate,
            data_rate=self._data_rate,
            source=source,
        )


def _parse_benchmark_json_v1(line: str) -> Optional[AlgorithmProgress]:
    text = str(line).strip()
    if not text.startswith(_PROGRESS_PREFIX):
        return None
    try:
        payload = json.loads(text[len(_PROGRESS_PREFIX) :])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    values = {
        name: _finite_number(payload.get(name))
        for name in ("timestamp", "frame", "fps", "percent")
    }
    if not any(value is not None for value in values.values()):
        return None
    return AlgorithmProgress(**values)


def _finite_number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
