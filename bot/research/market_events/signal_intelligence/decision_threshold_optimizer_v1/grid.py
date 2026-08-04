"""Decision Threshold Optimizer V1 — research-only grid search."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Any, Iterator


@dataclass(frozen=True)
class ThresholdSet:
    min_supporting: int
    min_confidence: float  # 0–1
    min_fingerprint: float  # 0–100 pct
    min_timeline: float  # 0–100 pct
    min_hist_wr: float  # 0–100
    min_hist_pf: float
    min_rules: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def label(self) -> str:
        return (
            f"sup>={self.min_supporting} conf>={int(self.min_confidence * 100)}% "
            f"fp>={self.min_fingerprint:g}% tl>={self.min_timeline:g}% "
            f"wr>={self.min_hist_wr:g}% pf>={self.min_hist_pf:g} rules>={self.min_rules}"
        )


# Search grid (as specified)
SUPPORTING_GRID = (1, 2, 3)
CONFIDENCE_GRID = (0.20, 0.30, 0.40, 0.50, 0.60, 0.70)
FINGERPRINT_GRID = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
TIMELINE_GRID = (20.0, 30.0, 40.0, 50.0, 60.0, 70.0)
HIST_WR_GRID = (40.0, 50.0, 60.0, 70.0)
HIST_PF_GRID = (1.0, 1.2, 1.5, 2.0)
RULES_GRID = (0, 1, 2)

# Current production Decision Engine thresholds (before)
BASELINE = ThresholdSet(
    min_supporting=2,
    min_confidence=0.45,
    min_fingerprint=5.0,
    min_timeline=30.0,
    min_hist_wr=55.0,
    min_hist_pf=1.15,
    min_rules=0,
)


def iter_threshold_grid() -> Iterator[ThresholdSet]:
    for sup, conf, fp, tl, wr, pf, rules in product(
        SUPPORTING_GRID,
        CONFIDENCE_GRID,
        FINGERPRINT_GRID,
        TIMELINE_GRID,
        HIST_WR_GRID,
        HIST_PF_GRID,
        RULES_GRID,
    ):
        yield ThresholdSet(
            min_supporting=int(sup),
            min_confidence=float(conf),
            min_fingerprint=float(fp),
            min_timeline=float(tl),
            min_hist_wr=float(wr),
            min_hist_pf=float(pf),
            min_rules=int(rules),
        )


def grid_size() -> int:
    return (
        len(SUPPORTING_GRID)
        * len(CONFIDENCE_GRID)
        * len(FINGERPRINT_GRID)
        * len(TIMELINE_GRID)
        * len(HIST_WR_GRID)
        * len(HIST_PF_GRID)
        * len(RULES_GRID)
    )


__all__ = [
    "BASELINE",
    "CONFIDENCE_GRID",
    "FINGERPRINT_GRID",
    "HIST_PF_GRID",
    "HIST_WR_GRID",
    "RULES_GRID",
    "SUPPORTING_GRID",
    "TIMELINE_GRID",
    "ThresholdSet",
    "grid_size",
    "iter_threshold_grid",
]
