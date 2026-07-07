"""Data quality metrics per chronological split."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.research.strategy_simulator.splits import market_start_ts

SECONDS_LEFT_THRESHOLDS: tuple[int, ...] = (240, 180, 120, 90, 60, 45, 30)
GAP_THRESHOLDS: tuple[int, ...] = (10, 20, 30)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _market_gaps(path: list[dict]) -> list[int]:
    gaps: list[int] = []
    for i in range(1, len(path)):
        gaps.append(int(path[i]["timestamp"]) - int(path[i - 1]["timestamp"]))
    return gaps


def _quote_complete(obs: dict, side: str) -> bool:
    if side == "YES":
        return obs.get("yes_bid") is not None and obs.get("yes_ask") is not None
    return obs.get("no_bid") is not None and obs.get("no_ask") is not None


@dataclass
class SplitDataQuality:
    name: str
    market_count: int = 0
    first_market_ts: int | None = None
    last_market_ts: int | None = None
    obs_per_market_median: float = 0.0
    obs_per_market_p10: float = 0.0
    obs_per_market_p25: float = 0.0
    obs_per_market_p50: float = 0.0
    obs_per_market_p75: float = 0.0
    obs_per_market_p90: float = 0.0
    first_seconds_left_median: float = 0.0
    last_seconds_left_median: float = 0.0
    seconds_left_coverage: dict[int, float] = field(default_factory=dict)
    yes_quote_completeness: float = 0.0
    no_quote_completeness: float = 0.0
    btc_delta_completeness: float = 0.0
    spread_completeness: float = 0.0
    snapshot_interval_median: float = 0.0
    gap_pct_over: dict[int, float] = field(default_factory=dict)
    total_rows: int = 0

    def to_dict(self) -> dict:
        return {
            "market_count": self.market_count,
            "first_market_ts": self.first_market_ts,
            "last_market_ts": self.last_market_ts,
            "obs_per_market_median": self.obs_per_market_median,
            "total_rows": self.total_rows,
        }


def analyze_split_data_quality(
    name: str,
    paths: dict[str, list[dict]],
) -> SplitDataQuality:
    q = SplitDataQuality(name=name)
    if not paths:
        return q

    q.market_count = len(paths)
    starts = [market_start_ts(paths[s]) for s in paths]
    q.first_market_ts = min(starts)
    q.last_market_ts = max(starts)

    obs_counts: list[float] = []
    first_sl: list[float] = []
    last_sl: list[float] = []
    all_intervals: list[float] = []
    yes_ok = no_ok = delta_ok = spread_ok = 0
    total_obs = 0

    markets_with_sl: dict[int, int] = {t: 0 for t in SECONDS_LEFT_THRESHOLDS}
    markets_with_gap: dict[int, int] = {t: 0 for t in GAP_THRESHOLDS}

    for slug, path in paths.items():
        n = len(path)
        obs_counts.append(float(n))
        total_obs += n
        if path:
            first_sl.append(float(path[0].get("seconds_left") or 0))
            last_sl.append(float(path[-1].get("seconds_left") or 0))
            max_sl = max(int(o.get("seconds_left") or 0) for o in path)
            for t in SECONDS_LEFT_THRESHOLDS:
                if max_sl >= t:
                    markets_with_sl[t] += 1
            gaps = _market_gaps(path)
            all_intervals.extend(gaps)
            for gt in GAP_THRESHOLDS:
                if any(g > gt for g in gaps):
                    markets_with_gap[gt] += 1
            for obs in path:
                if _quote_complete(obs, "YES"):
                    yes_ok += 1
                if _quote_complete(obs, "NO"):
                    no_ok += 1
                if obs.get("btc_price") is not None and (
                    obs.get("strike") is not None or obs.get("delta") is not None
                ):
                    delta_ok += 1
                if obs.get("yes_bid") is not None and obs.get("yes_ask") is not None:
                    spread_ok += 1

    q.total_rows = total_obs
    q.obs_per_market_p10 = _percentile(obs_counts, 0.10)
    q.obs_per_market_p25 = _percentile(obs_counts, 0.25)
    q.obs_per_market_p50 = _percentile(obs_counts, 0.50)
    q.obs_per_market_p75 = _percentile(obs_counts, 0.75)
    q.obs_per_market_p90 = _percentile(obs_counts, 0.90)
    q.obs_per_market_median = q.obs_per_market_p50
    q.first_seconds_left_median = _percentile(first_sl, 0.50)
    q.last_seconds_left_median = _percentile(last_sl, 0.50)
    q.seconds_left_coverage = {
        t: 100.0 * markets_with_sl[t] / q.market_count for t in SECONDS_LEFT_THRESHOLDS
    }
    q.gap_pct_over = {
        t: 100.0 * markets_with_gap[t] / q.market_count for t in GAP_THRESHOLDS
    }
    if total_obs:
        q.yes_quote_completeness = 100.0 * yes_ok / total_obs
        q.no_quote_completeness = 100.0 * no_ok / total_obs
        q.btc_delta_completeness = 100.0 * delta_ok / total_obs
        q.spread_completeness = 100.0 * spread_ok / total_obs
    q.snapshot_interval_median = _percentile(all_intervals, 0.50) if all_intervals else 0.0
    return q
