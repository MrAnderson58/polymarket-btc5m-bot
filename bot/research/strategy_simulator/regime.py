"""Regime diagnostics per split (BTC + Polymarket)."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median

from bot.research.strategy_simulator.data_quality import _percentile
from bot.research.strategy_simulator.splits import market_start_ts


@dataclass
class RegimeDiagnostics:
    name: str
    mean_abs_final_delta: float = 0.0
    median_abs_final_delta: float = 0.0
    close_abs_delta_pct: dict[str, float] = field(default_factory=dict)
    yes_ask_p25: float = 0.0
    yes_ask_p50: float = 0.0
    yes_ask_p75: float = 0.0
    no_ask_p25: float = 0.0
    no_ask_p50: float = 0.0
    no_ask_p75: float = 0.0
    median_spread: float = 0.0
    spread_le_1c_pct: float = 0.0
    spread_le_2c_pct: float = 0.0
    spread_le_5c_pct: float = 0.0


def _final_delta(path: list[dict]) -> float | None:
    if not path:
        return None
    last = path[-1]
    if last.get("delta") is not None:
        return float(last["delta"])
    strike = last.get("strike")
    if strike is None:
        return None
    return float(last["btc_price"]) - float(strike)


def analyze_regime(name: str, paths: dict[str, list[dict]]) -> RegimeDiagnostics:
    r = RegimeDiagnostics(name=name)
    if not paths:
        return r

    abs_deltas: list[float] = []
    yes_asks: list[float] = []
    no_asks: list[float] = []
    spreads: list[float] = []

    for path in paths.values():
        fd = _final_delta(path)
        if fd is not None:
            abs_deltas.append(abs(fd))
        for obs in path:
            ya, na = obs.get("yes_ask"), obs.get("no_ask")
            if ya is not None:
                yes_asks.append(float(ya))
            if na is not None:
                no_asks.append(float(na))
            yb, yask = obs.get("yes_bid"), obs.get("yes_ask")
            if yb is not None and yask is not None:
                spreads.append(max(0.0, float(yask) - float(yb)))

    if abs_deltas:
        r.mean_abs_final_delta = mean(abs_deltas)
        r.median_abs_final_delta = median(abs_deltas)
        n = len(abs_deltas)
        r.close_abs_delta_pct = {
            "<10": 100.0 * sum(1 for d in abs_deltas if d < 10) / n,
            "<25": 100.0 * sum(1 for d in abs_deltas if d < 25) / n,
            "<50": 100.0 * sum(1 for d in abs_deltas if d < 50) / n,
            ">50": 100.0 * sum(1 for d in abs_deltas if d > 50) / n,
            ">100": 100.0 * sum(1 for d in abs_deltas if d > 100) / n,
        }

    if yes_asks:
        r.yes_ask_p25 = _percentile(yes_asks, 0.25)
        r.yes_ask_p50 = _percentile(yes_asks, 0.50)
        r.yes_ask_p75 = _percentile(yes_asks, 0.75)
    if no_asks:
        r.no_ask_p25 = _percentile(no_asks, 0.25)
        r.no_ask_p50 = _percentile(no_asks, 0.50)
        r.no_ask_p75 = _percentile(no_asks, 0.75)
    if spreads:
        r.median_spread = _percentile(spreads, 0.50)
        r.spread_le_1c_pct = 100.0 * sum(1 for s in spreads if s <= 0.01) / len(spreads)
        r.spread_le_2c_pct = 100.0 * sum(1 for s in spreads if s <= 0.02) / len(spreads)
        r.spread_le_5c_pct = 100.0 * sum(1 for s in spreads if s <= 0.05) / len(spreads)
    return r


def observation_density_deciles(paths: dict[str, list[dict]]) -> list[tuple[int, int, float]]:
    """Return (decile, market_count, median_obs) for chronological deciles."""
    if not paths:
        return []
    ordered = sorted(paths.keys(), key=lambda s: (market_start_ts(paths[s]), s))
    n = len(ordered)
    deciles: list[tuple[int, int, float]] = []
    for d in range(10):
        start = (n * d) // 10
        end = (n * (d + 1)) // 10
        chunk = ordered[start:end]
        if not chunk:
            deciles.append((d + 1, 0, 0.0))
            continue
        counts = [len(paths[s]) for s in chunk]
        deciles.append((d + 1, len(chunk), float(median(counts))))
    return deciles


def detect_density_discontinuities(
    deciles: list[tuple[int, int, float]],
    *,
    ratio_threshold: float = 2.0,
) -> list[str]:
    warnings: list[str] = []
    for i in range(1, len(deciles)):
        _, _, prev_med = deciles[i - 1]
        d_num, n_mkts, med = deciles[i]
        if prev_med <= 0 or med <= 0:
            continue
        ratio = med / prev_med
        if ratio >= ratio_threshold or ratio <= 1.0 / ratio_threshold:
            warnings.append(
                f"Decile {d_num}: median obs {med:.0f} vs prev {prev_med:.0f} "
                f"(ratio {ratio:.2f})"
            )
    return warnings
