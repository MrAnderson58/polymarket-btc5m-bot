"""Observe-only forward signal tracker — no optimization on forward results."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from bot.research.strategy_simulator.deduplication import StrategyFamily
from bot.research.strategy_simulator.market_context import MarketPathContext
from bot.research.strategy_simulator.market_filter import MarketFilter, list_filtered_market_paths
from bot.research.strategy_simulator.simulator import (
    VirtualTrade,
    _entry_ask,
    _forward_exit,
    _matches_strategy,
    build_snapshot_features,
)
from bot.research.strategy_simulator.storage import (
    FORWARD_CANDIDATES_TABLE,
    FORWARD_SIGNALS_TABLE,
    ensure_forward_tables,
    insert_forward_signal,
    list_active_forward_candidates,
    register_forward_candidates,
)
from bot.research.strategy_simulator.strategies import Strategy


MIN_FORWARD_SAMPLE_PER_STRATEGY = 30
MIN_FORWARD_MARKETS_WITH_SIGNALS = 20


@dataclass(frozen=True)
class ForwardSignalRecord:
    strategy_fp: str
    market_slug: str
    signal_ts: int
    entry_ask: float
    btc_delta: float | None
    seconds_left: int
    spread: float | None
    tp: float
    max_bid_after: float
    final_bid: float
    tp_reached: bool
    simulated_pnl: float


def _max_bid_after(
    observations: list[dict],
    start_idx: int,
    *,
    direction: str,
) -> float:
    bid_key = "yes_bid" if direction == "YES" else "no_bid"
    entry_bid = observations[start_idx].get(bid_key)
    peak = float(entry_bid) if entry_bid is not None else 0.0
    for obs in observations[start_idx + 1:]:
        bid = obs.get(bid_key)
        if bid is not None:
            peak = max(peak, float(bid))
    return peak


def extract_forward_signal(
    observations: list[dict],
    idx: int,
    strategy: Strategy,
) -> ForwardSignalRecord | None:
    feat = build_snapshot_features(observations, idx, side=strategy.direction)
    if feat is None or not _matches_strategy(feat, strategy):
        return None
    entry = _entry_ask(feat, strategy.direction)
    assert entry is not None
    exit_price, _, won = _forward_exit(
        observations,
        idx,
        direction=strategy.direction,
        entry_price=entry,
        tp=strategy.tp,
    )
    bid_key = "yes_bid" if strategy.direction == "YES" else "no_bid"
    final_bid = float(observations[-1].get(bid_key) or exit_price)
    return ForwardSignalRecord(
        strategy_fp=strategy.fingerprint(),
        market_slug=str(observations[0]["market_slug"]),
        signal_ts=feat.timestamp,
        entry_ask=entry,
        btc_delta=feat.btc_delta,
        seconds_left=feat.seconds_left,
        spread=feat.spread_now,
        tp=strategy.tp,
        max_bid_after=_max_bid_after(observations, idx, direction=strategy.direction),
        final_bid=final_bid,
        tp_reached=won,
        simulated_pnl=exit_price - entry,
    )


def scan_market_forward_signals(
    observations: list[dict],
    strategies: list[Strategy],
    *,
    one_per_market: bool = True,
) -> list[ForwardSignalRecord]:
    records: list[ForwardSignalRecord] = []
    for strategy in strategies:
        found = False
        for idx in range(len(observations)):
            rec = extract_forward_signal(observations, idx, strategy)
            if rec is None:
                continue
            records.append(rec)
            found = True
            if one_per_market:
                break
        if found and one_per_market:
            continue
    return records


def register_top_families(
    conn: sqlite3.Connection,
    families: list[StrategyFamily],
    *,
    max_families: int = 3,
) -> list[str]:
    ensure_forward_tables(conn)
    selected = families[:max_families]
    payloads = []
    for i, family in enumerate(selected, start=1):
        s = family.representative.strategy
        payloads.append({
            "strategy_fp": s.fingerprint(),
            "parameters_json": s.to_json(),
            "family_label": f"family_{i}",
            "family_size": family.family_size,
        })
    return register_forward_candidates(conn, payloads, replace=True)


def run_forward_tracking(
    conn: sqlite3.Connection,
    *,
    market_filter: MarketFilter,
    strategies: list[Strategy] | None = None,
) -> list[ForwardSignalRecord]:
    """Record new forward signals for active candidates; skip existing keys."""
    ensure_forward_tables(conn)
    if strategies is None:
        candidates = list_active_forward_candidates(conn)
        strategies = [Strategy.from_json(c["parameters_json"]) for c in candidates]
    if not strategies:
        return []

    fps = {s.fingerprint() for s in strategies}
    existing = {
        (row["strategy_fp"], row["market_slug"], row["signal_ts"])
        for row in conn.execute(
            f"SELECT strategy_fp, market_slug, signal_ts FROM {FORWARD_SIGNALS_TABLE}"
        ).fetchall()
    }

    paths = list_filtered_market_paths(conn, market_filter)
    new_records: list[ForwardSignalRecord] = []
    for slug, path in paths.items():
        for rec in scan_market_forward_signals(path, strategies, one_per_market=True):
            if rec.strategy_fp not in fps:
                continue
            key = (rec.strategy_fp, rec.market_slug, rec.signal_ts)
            if key in existing:
                continue
            insert_forward_signal(conn, rec)
            new_records.append(rec)
            existing.add(key)
    return new_records


def forward_sample_status(conn: sqlite3.Connection) -> dict[str, dict]:
    ensure_forward_tables(conn)
    rows = conn.execute(
        f"""
        SELECT strategy_fp, COUNT(*) AS n,
               SUM(tp_reached) AS tp_hits,
               AVG(simulated_pnl) AS avg_pnl
        FROM {FORWARD_SIGNALS_TABLE}
        GROUP BY strategy_fp
        """
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        n = int(row["n"])
        out[row["strategy_fp"]] = {
            "signals": n,
            "tp_hits": int(row["tp_hits"] or 0),
            "avg_pnl": float(row["avg_pnl"] or 0),
            "ready_for_review": n >= MIN_FORWARD_SAMPLE_PER_STRATEGY,
            "target_signals": MIN_FORWARD_SAMPLE_PER_STRATEGY,
        }
    return out


def render_forward_status(conn: sqlite3.Connection) -> str:
    ensure_forward_tables(conn)
    candidates = list_active_forward_candidates(conn)
    status = forward_sample_status(conn)
    lines = [
        "FORWARD SHADOW VALIDATION (observe-only)",
        f"  active candidates: {len(candidates)}",
        f"  minimum forward sample target: {MIN_FORWARD_SAMPLE_PER_STRATEGY} signals/strategy",
        f"  minimum markets target: {MIN_FORWARD_MARKETS_WITH_SIGNALS}",
    ]
    for c in candidates:
        fp = c["strategy_fp"]
        st = status.get(fp, {"signals": 0, "tp_hits": 0, "avg_pnl": 0.0, "ready_for_review": False})
        lines.append(
            f"  {c['family_label']} fp={fp[:12]}… signals={st['signals']} "
            f"tp_hits={st['tp_hits']} avg_pnl={st['avg_pnl']:.4f} "
            f"ready={st['ready_for_review']}"
        )
    return "\n".join(lines)
