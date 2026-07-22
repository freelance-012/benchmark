"""Select a dataset-type rule and build persisted Segment records."""

from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..contracts import MIN_SEGMENT_DURATION_SECONDS, MIN_SEGMENT_FRAMES
from ..errors import DatasetError
from ..models import ScanDiagnostic, Segment
from ..parsers import ImuStateRecord
from .flight_mode import segment_by_flight_mode
from .timestamp import segment_by_timestamp


def build_segments(
    rule: str,
    root: Path,
    dataset_id: str,
    image_timestamps: Sequence[float],
    imu_records: Optional[Sequence[ImuStateRecord]] = None,
) -> Tuple[List[Segment], List[ScanDiagnostic]]:
    """Apply one contract-bound rule and build Segment records."""

    normalized = str(rule).strip().lower()
    if normalized == "flight_mode":
        if imu_records is None:
            raise DatasetError("flight_mode segmentation requires IMU records")
        ranges = segment_by_flight_mode(imu_records)
    elif normalized == "timestamp":
        ranges = segment_by_timestamp(image_timestamps)
    else:
        raise DatasetError(f"unsupported segmentation rule: {rule}")

    segments: List[Segment] = []
    diagnostics: List[ScanDiagnostic] = []
    for sequence_no, (start_timestamp, end_timestamp) in enumerate(ranges, start=1):
        frame_count = _count_frames(
            image_timestamps,
            start_timestamp,
            end_timestamp,
        )
        duration_seconds = round(end_timestamp - start_timestamp, 9)
        invalid_reason = _invalid_reason(frame_count, duration_seconds)
        valid = invalid_reason is None
        segments.append(
            Segment(
                segment_id=_segment_id(
                    dataset_id,
                    start_timestamp,
                    end_timestamp,
                ),
                sequence_no=sequence_no,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                duration_seconds=duration_seconds,
                frame_count=frame_count,
                valid=valid,
                invalid_reason=invalid_reason,
            )
        )
        if not valid:
            diagnostics.append(
                ScanDiagnostic(
                    "warning",
                    "segment_too_short",
                    root,
                    (
                        f"Segment {sequence_no} 只有 {frame_count} 个图像帧、"
                        f"持续 {duration_seconds:g} 秒"
                    ),
                )
            )

    if not ranges:
        diagnostics.append(
            ScanDiagnostic(
                "error",
                "no_active_segment",
                root,
                "分段规则没有找到可运行区间",
            )
        )
    elif not any(segment.valid for segment in segments):
        diagnostics.append(
            ScanDiagnostic(
                "error",
                "no_valid_segment",
                root,
                (
                    f"没有同时达到 {MIN_SEGMENT_FRAMES} 个图像帧和 "
                    f"{MIN_SEGMENT_DURATION_SECONDS:g} 秒的有效 Segment"
                ),
            )
        )
    return segments, diagnostics


def segments_are_current(
    dataset_id: str,
    segments: Sequence[Segment],
) -> bool:
    """Check stable identity and the 200-frame/10-second validity rule."""

    for sequence_no, segment in enumerate(segments, start=1):
        expected_reason = _invalid_reason(
            segment.frame_count,
            segment.duration_seconds,
        )
        if (
            segment.sequence_no != sequence_no
            or segment.end_timestamp < segment.start_timestamp
            or segment.segment_id
            != _segment_id(
                dataset_id,
                segment.start_timestamp,
                segment.end_timestamp,
            )
            or abs(
                segment.duration_seconds
                - (segment.end_timestamp - segment.start_timestamp)
            )
            > 1e-8
            or segment.frame_count < 0
            or segment.valid != (expected_reason is None)
            or segment.invalid_reason != expected_reason
        ):
            return False
    return True


def _count_frames(
    timestamps: Sequence[float],
    start_timestamp: float,
    end_timestamp: float,
) -> int:
    start = bisect_left(timestamps, start_timestamp)
    end = bisect_right(timestamps, end_timestamp)
    return max(0, end - start)


def _invalid_reason(frame_count: int, duration_seconds: float) -> Optional[str]:
    too_few_frames = frame_count < MIN_SEGMENT_FRAMES
    too_short = duration_seconds < MIN_SEGMENT_DURATION_SECONDS
    if too_few_frames and too_short:
        return (
            f"fewer_than_{MIN_SEGMENT_FRAMES}_frames_and_"
            f"shorter_than_{MIN_SEGMENT_DURATION_SECONDS:g}_seconds"
        )
    if too_few_frames:
        return f"fewer_than_{MIN_SEGMENT_FRAMES}_image_frames"
    if too_short:
        return f"shorter_than_{MIN_SEGMENT_DURATION_SECONDS:g}_seconds"
    return None


def _segment_id(dataset_id: str, start: float, end: float) -> str:
    identity = f"{dataset_id}\0{start:.9f}\0{end:.9f}"
    return "seg-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "build_segments",
    "segment_by_flight_mode",
    "segment_by_timestamp",
    "segments_are_current",
]
