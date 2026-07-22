"""Segment ranges derived only from an ordered timestamp sequence."""

from __future__ import annotations

from typing import List, Sequence, Tuple


def segment_by_timestamp(
    timestamps: Sequence[float],
) -> List[Tuple[float, float]]:
    """Treat one timestamp sequence as one candidate Segment."""

    if not timestamps:
        return []
    return [(timestamps[0], timestamps[-1])]
