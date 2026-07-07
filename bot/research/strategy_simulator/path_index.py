"""Timestamp index helpers for observation paths."""

from __future__ import annotations

import bisect


def find_idx_at_or_before(timestamps: list[int], idx: int, target_ts: int) -> int | None:
    """Earliest observation index in backward scan window with ts <= target_ts."""
    if idx < 0:
        return None
    sub = timestamps[: idx + 1]
    lo_ts = target_ts - 5
    last_below = bisect.bisect_left(sub, lo_ts) - 1
    low_bound = 0 if last_below < 0 else last_below
    if sub[low_bound] <= target_ts:
        return low_bound
    return None
