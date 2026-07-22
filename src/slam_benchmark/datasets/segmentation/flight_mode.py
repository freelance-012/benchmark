"""Segment ranges derived from SF flight-mode state transitions."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..parsers import ImuStateRecord


def segment_by_flight_mode(
    records: Sequence[ImuStateRecord],
) -> List[Tuple[float, float]]:
    """Split at transitions between zero and non-zero flight mode."""

    ranges: List[Tuple[float, float]] = []
    start: Optional[float] = None
    last_active: Optional[float] = None

    for record in records:
        if record.flight_mode != 0:
            if start is None:
                start = record.timestamp
            last_active = record.timestamp
        elif start is not None and last_active is not None:
            ranges.append((start, last_active))
            start = None
            last_active = None

    if start is not None and last_active is not None:
        ranges.append((start, last_active))
    return ranges
