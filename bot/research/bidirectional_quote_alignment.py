"""Quote timestamp alignment study for bidirectional live shadow trades.

Investigates ENTRY_NOT_ASK / EXIT_NOT_BID suspects from live audit.
Live shadow records entry_ts = int(time.time()) with quotes from bot.main cycle;
audit historically compared against v4_shadow_observations (different write path/timing).

This module does NOT modify runtime, execution, or trading logic.

Usage:
  python -m bot.research.bidirectional_quote_alignment
  python -m bot.research.bidirectional_quote_alignment --trade-id 42
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from typing import Any

from bot.research.bidirectional_live_audit import (
    LiveTrade,
    dedupe_first_trade_per_market,
    load_closed_trades,
)

REPORT_WIDTH = 72
TOLERANCE = 0.03


@dataclass
class QuoteSnapshot:
    source: str
    timestamp: int | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    lag_sec: int | None = None  # trade_ts - quote_ts (positive = quote before trade)


@dataclass
class AlignmentRow:
    trade: LiveTrade
    entry_sources: list[QuoteSnapshot]
    exit_sources: list[QuoteSnapshot]
    bidi_obs_entry: QuoteSnapshot | None
    best_entry_match: QuoteSnapshot | None
    best_exit_match: QuoteSnapshot | None
    entry_delta: float | None
    exit_delta: float | None
    legacy_v4_entry_flag: bool
    legacy_v4_exit_flag: bool


def quote_v4_at_or_before(
    conn: sqlite3.Connection, market_slug: str, ts: int,
) -> QuoteSnapshot | None:
    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, no_bid, no_ask, timestamp
        FROM v4_shadow_observations
        WHERE market_slug = ? AND timestamp <= ?
        ORDER BY timestamp DESC LIMIT 1
        """,
        (market_slug, ts),
    ).fetchone()
    if not row:
        return None
    qts = int(row["timestamp"])
    return QuoteSnapshot(
        source="v4_at_or_before",
        timestamp=qts,
        yes_bid=row["yes_bid"],
        yes_ask=row["yes_ask"],
        no_bid=row["no_bid"],
        no_ask=row["no_ask"],
        lag_sec=ts - qts,
    )


def quote_v4_nearest(
    conn: sqlite3.Connection, market_slug: str, ts: int, window_sec: int = 15,
) -> QuoteSnapshot | None:
    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, no_bid, no_ask, timestamp,
               ABS(timestamp - ?) AS dist
        FROM v4_shadow_observations
        WHERE market_slug = ?
          AND timestamp BETWEEN ? AND ?
        ORDER BY dist ASC, timestamp DESC
        LIMIT 1
        """,
        (ts, market_slug, ts - window_sec, ts + window_sec),
    ).fetchone()
    if not row:
        return None
    qts = int(row["timestamp"])
    return QuoteSnapshot(
        source=f"v4_nearest_±{window_sec}s",
        timestamp=qts,
        yes_bid=row["yes_bid"],
        yes_ask=row["yes_ask"],
        no_bid=row["no_bid"],
        no_ask=row["no_ask"],
        lag_sec=ts - qts,
    )


def quote_market_check_at_or_before(
    conn: sqlite3.Connection, market_slug: str, ts: int,
) -> QuoteSnapshot | None:
    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, no_bid, no_ask, checked_at,
               cast(strftime('%s', checked_at) AS integer) AS ts_unix
        FROM market_checks
        WHERE market_slug = ?
          AND cast(strftime('%s', checked_at) AS integer) <= ?
        ORDER BY checked_at DESC
        LIMIT 1
        """,
        (market_slug, ts),
    ).fetchone()
    if not row or row["ts_unix"] is None:
        return None
    qts = int(row["ts_unix"])
    return QuoteSnapshot(
        source="market_check_at_or_before",
        timestamp=qts,
        yes_bid=row["yes_bid"],
        yes_ask=row["yes_ask"],
        no_bid=row["no_bid"],
        no_ask=row["no_ask"],
        lag_sec=ts - qts,
    )


def quote_market_check_nearest(
    conn: sqlite3.Connection, market_slug: str, ts: int, window_sec: int = 15,
) -> QuoteSnapshot | None:
    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, no_bid, no_ask, checked_at,
               cast(strftime('%s', checked_at) AS integer) AS ts_unix,
               ABS(cast(strftime('%s', checked_at) AS integer) - ?) AS dist
        FROM market_checks
        WHERE market_slug = ?
          AND cast(strftime('%s', checked_at) AS integer) BETWEEN ? AND ?
        ORDER BY dist ASC, checked_at DESC
        LIMIT 1
        """,
        (ts, market_slug, ts - window_sec, ts + window_sec),
    ).fetchone()
    if not row or row["ts_unix"] is None:
        return None
    qts = int(row["ts_unix"])
    return QuoteSnapshot(
        source=f"market_check_nearest_±{window_sec}s",
        timestamp=qts,
        yes_bid=row["yes_bid"],
        yes_ask=row["yes_ask"],
        no_bid=row["no_bid"],
        no_ask=row["no_ask"],
        lag_sec=ts - qts,
    )


def quote_bidi_observation_at_entry(
    conn: sqlite3.Connection, trade: LiveTrade,
) -> QuoteSnapshot | None:
    row = conn.execute(
        """
        SELECT entry_price, timestamp
        FROM bidirectional_shadow_observations
        WHERE market_slug = ? AND timestamp = ? AND decision = ?
        LIMIT 1
        """,
        (trade.market_slug, trade.entry_ts, trade.side),
    ).fetchone()
    if not row:
        return None
    price = float(row["entry_price"]) if row["entry_price"] is not None else None
    ask = price if trade.side == "YES" else None
    no_ask = price if trade.side == "NO" else None
    qts = int(row["timestamp"])
    return QuoteSnapshot(
        source="bidi_shadow_obs_at_entry",
        timestamp=qts,
        yes_bid=None,
        yes_ask=ask,
        no_bid=None,
        no_ask=no_ask,
        lag_sec=trade.entry_ts - qts,
    )


def _side_ask(snap: QuoteSnapshot, side: str) -> float | None:
    return snap.yes_ask if side == "YES" else snap.no_ask


def _side_bid(snap: QuoteSnapshot, side: str) -> float | None:
    return snap.yes_bid if side == "YES" else snap.no_bid


def _best_entry(sources: list[QuoteSnapshot], side: str, entry_price: float) -> tuple[QuoteSnapshot | None, float | None]:
    best: QuoteSnapshot | None = None
    best_delta: float | None = None
    for snap in sources:
        ask = _side_ask(snap, side)
        if ask is None or ask <= 0:
            continue
        delta = abs(ask - entry_price)
        if best is None or delta < best_delta:
            best = snap
            best_delta = delta
    return best, best_delta


def _best_exit(sources: list[QuoteSnapshot], side: str, exit_price: float) -> tuple[QuoteSnapshot | None, float | None]:
    best: QuoteSnapshot | None = None
    best_delta: float | None = None
    for snap in sources:
        bid = _side_bid(snap, side)
        if bid is None or bid <= 0:
            continue
        delta = abs(bid - exit_price)
        if best is None or delta < best_delta:
            best = snap
            best_delta = delta
    return best, best_delta


def analyze_trade(conn: sqlite3.Connection, trade: LiveTrade) -> AlignmentRow:
    entry_sources = [
        s for s in (
            quote_v4_at_or_before(conn, trade.market_slug, trade.entry_ts),
            quote_v4_nearest(conn, trade.market_slug, trade.entry_ts),
            quote_market_check_at_or_before(conn, trade.market_slug, trade.entry_ts),
            quote_market_check_nearest(conn, trade.market_slug, trade.entry_ts),
        ) if s is not None
    ]
    bidi_obs = quote_bidi_observation_at_entry(conn, trade)
    if bidi_obs:
        entry_sources.append(bidi_obs)

    exit_sources: list[QuoteSnapshot] = []
    if trade.exit_ts:
        exit_sources = [
            s for s in (
                quote_v4_at_or_before(conn, trade.market_slug, trade.exit_ts),
                quote_v4_nearest(conn, trade.market_slug, trade.exit_ts),
                quote_market_check_at_or_before(conn, trade.market_slug, trade.exit_ts),
                quote_market_check_nearest(conn, trade.market_slug, trade.exit_ts),
            ) if s is not None
        ]

    best_entry, entry_delta = _best_entry(entry_sources, trade.side, trade.entry_price)
    best_exit, exit_delta = (None, None)
    if trade.exit_price is not None and exit_sources:
        best_exit, exit_delta = _best_exit(exit_sources, trade.side, trade.exit_price)

    v4_entry = quote_v4_at_or_before(conn, trade.market_slug, trade.entry_ts)
    v4_exit = quote_v4_at_or_before(conn, trade.market_slug, trade.exit_ts) if trade.exit_ts else None
    legacy_entry = False
    legacy_exit = False
    if v4_entry:
        ask = _side_ask(v4_entry, trade.side)
        if ask and abs(ask - trade.entry_price) > TOLERANCE:
            legacy_entry = True
    if v4_exit and trade.exit_price is not None:
        bid = _side_bid(v4_exit, trade.side)
        if bid and abs(bid - trade.exit_price) > TOLERANCE:
            legacy_exit = True

    return AlignmentRow(
        trade=trade,
        entry_sources=entry_sources,
        exit_sources=exit_sources,
        bidi_obs_entry=bidi_obs,
        best_entry_match=best_entry,
        best_exit_match=best_exit,
        entry_delta=entry_delta,
        exit_delta=exit_delta,
        legacy_v4_entry_flag=legacy_entry,
        legacy_v4_exit_flag=legacy_exit,
    )


def run_alignment_study(
    conn: sqlite3.Connection,
    *,
    corrected: bool = True,
    trade_id: int | None = None,
) -> dict[str, Any]:
    trades = load_closed_trades(conn)
    if corrected:
        trades, dedup = dedupe_first_trade_per_market(trades)
    else:
        dedup = {"raw": len(trades), "corrected": len(trades), "duplicates_removed": 0}

    if trade_id is not None:
        trades = [t for t in trades if t.id == trade_id]

    rows = [analyze_trade(conn, t) for t in trades]

    legacy_entry_flags = sum(1 for r in rows if r.legacy_v4_entry_flag)
    legacy_exit_flags = sum(1 for r in rows if r.legacy_v4_exit_flag)

    entry_within_tol = sum(
        1 for r in rows if r.entry_delta is not None and r.entry_delta <= TOLERANCE
    )
    exit_within_tol = sum(
        1 for r in rows if r.exit_delta is not None and r.exit_delta <= TOLERANCE
    )
    exit_checked = sum(1 for r in rows if r.exit_delta is not None)

    by_best_entry_source: dict[str, int] = {}
    for r in rows:
        if r.best_entry_match and r.entry_delta is not None and r.entry_delta <= TOLERANCE:
            by_best_entry_source[r.best_entry_match.source] = (
                by_best_entry_source.get(r.best_entry_match.source, 0) + 1
            )

    bidi_obs_exact = sum(
        1 for r in rows
        if r.bidi_obs_entry
        and _side_ask(r.bidi_obs_entry, r.trade.side) is not None
        and abs(_side_ask(r.bidi_obs_entry, r.trade.side) - r.trade.entry_price) <= TOLERANCE
    )

    lag_buckets = {"0-3s": 0, "4-10s": 0, "11-30s": 0, "31+s": 0, "no_v4": 0}
    for r in rows:
        v4 = quote_v4_at_or_before(conn, r.trade.market_slug, r.trade.entry_ts)
        if not v4 or v4.lag_sec is None:
            lag_buckets["no_v4"] += 1
        elif v4.lag_sec <= 3:
            lag_buckets["0-3s"] += 1
        elif v4.lag_sec <= 10:
            lag_buckets["4-10s"] += 1
        elif v4.lag_sec <= 30:
            lag_buckets["11-30s"] += 1
        else:
            lag_buckets["31+s"] += 1

    suspects = [r for r in rows if r.legacy_v4_entry_flag or r.legacy_v4_exit_flag]

    return {
        "trade_count": len(rows),
        "dedup": dedup,
        "legacy_v4_entry_flags": legacy_entry_flags,
        "legacy_v4_exit_flags": legacy_exit_flags,
        "entry_match_within_tol": entry_within_tol,
        "exit_match_within_tol": exit_within_tol,
        "exit_checked": exit_checked,
        "bidi_obs_entry_exact": bidi_obs_exact,
        "best_entry_source_counts": by_best_entry_source,
        "v4_quote_lag_buckets": lag_buckets,
        "suspects": suspects,
        "rows": rows,
    }


def render_report(study: dict[str, Any]) -> str:
    lines: list[str] = []
    w = REPORT_WIDTH

    def h(title: str) -> None:
        lines.append("")
        lines.append("=" * w)
        lines.append(title)
        lines.append("=" * w)

    h("BIDIRECTIONAL QUOTE ALIGNMENT STUDY (READ-ONLY)")
    lines.append(
        "Purpose: verify whether ENTRY_NOT_ASK / EXIT_NOT_BID are timestamp/path "
        "artifacts, not proven execution bugs."
    )
    lines.append(f"Trades analyzed: {study['trade_count']}")
    dedup = study["dedup"]
    if dedup.get("duplicates_removed", 0) > 0:
        lines.append(
            f"Dedup: raw={dedup['raw']} corrected={dedup['corrected']} "
            f"removed={dedup['duplicates_removed']}"
        )

    h("1. LEGACY AUDIT FLAGS (v4 at-or-before)")
    lines.append(
        f"  ENTRY_NOT_ASK equivalent: {study['legacy_v4_entry_flags']} / {study['trade_count']}"
    )
    lines.append(
        f"  EXIT_NOT_BID equivalent:  {study['legacy_v4_exit_flags']} / {study['trade_count']}"
    )
    lines.append(
        "  Note: live shadow uses bot.main cycle quotes; v4_obs may lag or lead."
    )

    h("2. BEST-SOURCE MATCH (any source, ±15s nearest or at-or-before)")
    n = study["trade_count"]
    lines.append(
        f"  Entry within {TOLERANCE}: {study['entry_match_within_tol']}/{n} "
        f"({100*study['entry_match_within_tol']/n:.1f}%)" if n else "  Entry: no trades"
    )
    if study["exit_checked"]:
        ec = study["exit_checked"]
        lines.append(
            f"  Exit within {TOLERANCE}:  {study['exit_match_within_tol']}/{ec} "
            f"({100*study['exit_match_within_tol']/ec:.1f}%)"
        )
    lines.append(
        f"  bidi_shadow_obs exact entry: {study['bidi_obs_entry_exact']}/{n}"
    )

    h("3. BEST ENTRY SOURCE (when match within tolerance)")
    for source, count in sorted(study["best_entry_source_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"  {source:<35} {count:>4}")

    h("4. V4 QUOTE LAG AT ENTRY (trade_ts - v4_ts, at-or-before)")
    for bucket, count in study["v4_quote_lag_buckets"].items():
        lines.append(f"  {bucket:<8} {count:>4}")

    h("5. VERDICT")
    legacy = study["legacy_v4_entry_flags"] + study["legacy_v4_exit_flags"]
    matched = study["entry_match_within_tol"]
    if n and matched / n >= 0.85:
        lines.append(
            f"  Quote mismatches largely explained by source/timing: "
            f"{matched}/{n} entries align with best available quote."
        )
        lines.append(
            "  Do NOT treat legacy ENTRY_NOT_ASK/EXIT_NOT_BID as execution errors without"
        )
        lines.append("  confirming market_checks or bidi_shadow_obs at entry_ts.")
    elif legacy > 0:
        lines.append(
            f"  {legacy} legacy flags remain; inspect per-trade detail below."
        )
    else:
        lines.append("  No legacy v4 mismatches detected.")

    suspects = study["suspects"][:20]
    if suspects:
        h("6. TOP SUSPECTS (legacy flag, best match shown)")
        lines.append(f"  {'ID':>5} {'Side':<4} {'Entry':>6} {'BestΔ':>6} {'Source':<28} {'Lag':>5}")
        for r in suspects:
            best = r.best_entry_match
            src = best.source if best else "-"
            lag = best.lag_sec if best and best.lag_sec is not None else "-"
            delta = f"{r.entry_delta:.3f}" if r.entry_delta is not None else "-"
            lines.append(
                f"  {r.trade.id:>5} {r.trade.side:<4} {r.trade.entry_price:>6.3f} "
                f"{delta:>6} {src:<28} {str(lag):>5}"
            )
        if len(study["suspects"]) > 20:
            lines.append(f"  ... +{len(study['suspects']) - 20} more")

    lines.append("")
    return "\n".join(lines)


def render_trade_detail(row: AlignmentRow) -> str:
    lines = [
        f"Trade #{row.trade.id} {row.trade.side} {row.trade.market_slug}",
        f"  entry={row.trade.entry_price:.3f} @ ts={row.trade.entry_ts}  "
        f"exit={row.trade.exit_price} @ ts={row.trade.exit_ts}",
        "  Entry sources:",
    ]
    for s in row.entry_sources:
        ask = _side_ask(s, row.trade.side)
        lines.append(
            f"    {s.source:<30} ts={s.timestamp} lag={s.lag_sec}s "
            f"ask={ask} Δ={abs(ask - row.trade.entry_price):.3f}" if ask else
            f"    {s.source:<30} ts={s.timestamp} lag={s.lag_sec}s ask=?"
        )
    if row.exit_sources:
        lines.append("  Exit sources:")
        for s in row.exit_sources:
            bid = _side_bid(s, row.trade.side)
            ep = row.trade.exit_price or 0
            lines.append(
                f"    {s.source:<30} ts={s.timestamp} lag={s.lag_sec}s "
                f"bid={bid} Δ={abs(bid - ep):.3f}" if bid and row.trade.exit_price else
                f"    {s.source:<30} ts={s.timestamp} lag={s.lag_sec}s bid=?"
            )
    return "\n".join(lines)


def main() -> int:
    from bot.database import connect, init_db
    from bot.strategy.bidirectional_shadow import ensure_tables

    parser = argparse.ArgumentParser(description="Quote alignment study (read-only)")
    parser.add_argument("--trade-id", type=int, default=None, help="Single trade detail")
    parser.add_argument("--raw", action="store_true", help="Include duplicate markets")
    args = parser.parse_args()

    init_db()
    with connect() as conn:
        ensure_tables(conn)
        study = run_alignment_study(
            conn, corrected=not args.raw, trade_id=args.trade_id,
        )
        print(render_report(study))
        if args.trade_id and study["rows"]:
            print(render_trade_detail(study["rows"][0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
