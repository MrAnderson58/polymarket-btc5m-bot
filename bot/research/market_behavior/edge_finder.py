"""Brute-force search for high-probability multidimensional edge combinations."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from bot.research.market_behavior.config import EDGE_TP_LEVELS, MIN_EDGE_SAMPLES, PRIMARY_EV_TP
from bot.research.market_behavior.edge_statistics import EdgeObservation, compute_ev, pnl_for_tp
from bot.research.market_behavior.tp_probability import aggregate_tp_probabilities


@dataclass
class EdgeCell:
    direction: str
    entry_bucket: str
    btc_delta_bucket: str
    seconds_left_bucket: str
    spread_bucket: str
    samples: int = 0
    tp_probs: dict[float, float] = field(default_factory=dict)
    avg_final_delta: float = 0.0
    avg_move_to_close: float = 0.0
    avg_spread: float = 0.0
    avg_max_excursion: float = 0.0
    avg_adverse_excursion: float = 0.0
    avg_entry_price: float = 0.0
    ev_by_tp: dict[float, float] = field(default_factory=dict)
    expected_profit_by_tp: dict[float, float] = field(default_factory=dict)
    expected_loss_by_tp: dict[float, float] = field(default_factory=dict)

    @property
    def combo_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.direction,
            self.entry_bucket,
            self.btc_delta_bucket,
            self.seconds_left_bucket,
            self.spread_bucket,
        )

    @property
    def primary_ev(self) -> float:
        return self.ev_by_tp.get(PRIMARY_EV_TP, 0.0)

    @property
    def primary_tp_prob(self) -> float:
        return self.tp_probs.get(PRIMARY_EV_TP, 0.0)


def aggregate_edge_observations(observations: list[EdgeObservation]) -> list[EdgeCell]:
    """Group edge observations by full bucket combination."""
    groups: dict[tuple[str, str, str, str, str], list[EdgeObservation]] = {}
    for obs in observations:
        key = (
            obs.direction,
            obs.entry_bucket,
            obs.btc_delta_bucket,
            obs.seconds_left_bucket,
            obs.spread_bucket,
        )
        groups.setdefault(key, []).append(obs)

    cells: list[EdgeCell] = []
    for (direction, entry_b, delta_b, sl_b, sp_b), rows in groups.items():
        n = len(rows)
        cell = EdgeCell(
            direction=direction,
            entry_bucket=entry_b,
            btc_delta_bucket=delta_b,
            seconds_left_bucket=sl_b,
            spread_bucket=sp_b,
            samples=n,
            avg_final_delta=mean(r.final_delta for r in rows if r.final_delta is not None),
            avg_move_to_close=mean(r.move_to_close for r in rows if r.move_to_close is not None),
            avg_spread=mean(r.spread_cents for r in rows),
            avg_max_excursion=mean(r.max_excursion for r in rows if r.max_excursion is not None),
            avg_adverse_excursion=mean(r.adverse_excursion for r in rows if r.adverse_excursion is not None),
            avg_entry_price=mean(r.entry_price for r in rows),
        )
        cell.tp_probs = aggregate_tp_probabilities(rows)
        for tp in EDGE_TP_LEVELS:
            losses = []
            for r in rows:
                if r.tp_reached.get(tp) is True:
                    continue
                if r.tp_reached.get(tp) is None:
                    continue
                losses.append(max(0.0, r.entry_price - (r.final_bid or 0.0)))
            avg_loss = mean(losses) if losses else 0.0
            prob = cell.tp_probs.get(tp, 0.0)
            exp_profit, exp_loss, _ev = compute_ev(cell.avg_entry_price, tp, prob, avg_loss)
            cell.expected_profit_by_tp[tp] = exp_profit
            cell.expected_loss_by_tp[tp] = exp_loss
            eligible = [r for r in rows if r.tp_reached.get(tp) is not None]
            if eligible:
                cell.ev_by_tp[tp] = mean(pnl_for_tp(r, tp) for r in eligible)
            else:
                cell.ev_by_tp[tp] = 0.0

        cells.append(cell)
    return cells


def find_best_edges(
    cells: list[EdgeCell],
    *,
    min_samples: int | None = None,
    top_n: int = 20,
) -> list[EdgeCell]:
    """Rank cells by sample size, then TP probability, then EV."""
    floor = min_samples or MIN_EDGE_SAMPLES
    eligible = [c for c in cells if c.samples >= floor]
    return sorted(
        eligible,
        key=lambda c: (c.samples, c.primary_tp_prob, c.primary_ev),
        reverse=True,
    )[:top_n]


def find_all_combinations(cells: list[EdgeCell]) -> list[EdgeCell]:
    """Return every aggregated cell (full brute-force enumeration)."""
    return sorted(cells, key=lambda c: c.combo_key)
