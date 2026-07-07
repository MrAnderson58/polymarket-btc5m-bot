"""Market-level bootstrap confidence intervals."""

from __future__ import annotations

from dataclasses import dataclass
import random
from statistics import mean

from bot.research.strategy_simulator.simulator import VirtualTrade


@dataclass
class BootstrapResult:
    ev_mean: float
    ev_ci_low: float
    ev_ci_high: float
    win_rate_mean: float
    win_rate_ci_low: float
    win_rate_ci_high: float
    pf_median: float
    pf_ci_low: float
    pf_ci_high: float
    prob_ev_positive: float
    n_samples: int
    seed: int

    def to_dict(self) -> dict:
        return {
            "ev_mean": self.ev_mean,
            "ev_ci_low": self.ev_ci_low,
            "ev_ci_high": self.ev_ci_high,
            "win_rate_mean": self.win_rate_mean,
            "win_rate_ci_low": self.win_rate_ci_low,
            "win_rate_ci_high": self.win_rate_ci_high,
            "pf_median": self.pf_median,
            "pf_ci_low": self.pf_ci_low,
            "pf_ci_high": self.pf_ci_high,
            "prob_ev_positive": self.prob_ev_positive,
            "n_samples": self.n_samples,
            "seed": self.seed,
        }


def _market_pnls(trades: list[VirtualTrade]) -> dict[str, list[float]]:
    by_market: dict[str, list[float]] = {}
    for t in trades:
        by_market.setdefault(t.market_slug, []).append(t.pnl)
    return by_market


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(q * (len(ordered) - 1))
    return ordered[idx]


def bootstrap_market_metrics(
    trades: list[VirtualTrade],
    *,
    n_samples: int = 1000,
    seed: int = 42,
    ci: float = 0.95,
) -> BootstrapResult:
    """Resample markets with replacement; aggregate per-market mean PnL."""
    by_market = _market_pnls(trades)
    markets = list(by_market.keys())
    if not markets:
        return BootstrapResult(
            ev_mean=0.0, ev_ci_low=0.0, ev_ci_high=0.0,
            win_rate_mean=0.0, win_rate_ci_low=0.0, win_rate_ci_high=0.0,
            pf_median=0.0, pf_ci_low=0.0, pf_ci_high=0.0,
            prob_ev_positive=0.0, n_samples=n_samples, seed=seed,
        )

    rng = random.Random(seed)
    evs: list[float] = []
    win_rates: list[float] = []
    pfs: list[float] = []

    for _ in range(n_samples):
        sample_markets = [rng.choice(markets) for _ in range(len(markets))]
        pnls: list[float] = []
        for m in sample_markets:
            pnls.extend(by_market[m])
        if not pnls:
            continue
        evs.append(mean(pnls))
        wins = sum(1 for p in pnls if p > 0)
        win_rates.append(wins / len(pnls))
        gross_win = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p <= 0))
        pfs.append(gross_win / gross_loss if gross_loss > 0 else float("inf"))

    alpha = (1.0 - ci) / 2.0
    return BootstrapResult(
        ev_mean=mean(evs) if evs else 0.0,
        ev_ci_low=_percentile(evs, alpha),
        ev_ci_high=_percentile(evs, 1.0 - alpha),
        win_rate_mean=mean(win_rates) if win_rates else 0.0,
        win_rate_ci_low=_percentile(win_rates, alpha),
        win_rate_ci_high=_percentile(win_rates, 1.0 - alpha),
        pf_median=_percentile(pfs, 0.5),
        pf_ci_low=_percentile(pfs, alpha),
        pf_ci_high=_percentile(pfs, 1.0 - alpha),
        prob_ev_positive=sum(1 for e in evs if e > 0) / len(evs) if evs else 0.0,
        n_samples=n_samples,
        seed=seed,
    )
