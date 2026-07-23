"""S55.3 — Portfolio Manager for S42 paper book.

Keeps open count under S55_MAX_OPEN_TRADES by:
1. Force-closing stale / dead opens (age >> timeout, or no price path).
2. Replacing weakest open with a stronger gated candidate (Remaining Expected PnL).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

EXIT_PORTFOLIO_REPLACE = "PORTFOLIO_REPLACE"
EXIT_STALE = "STALE"

# How far past TIMEOUT before we treat as dead even without a live price.
S55_STALE_MULTIPLIER = 1.0  # age >= TIMEOUT * multiplier → force close
S55_PORTFOLIO_REPLACE_ENABLED = True
S55_MIN_REPLACE_EDGE_PCT = 0.05  # candidate must beat weakest by this expected PnL pct


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_portfolio_config_from_env() -> None:
    global S55_STALE_MULTIPLIER, S55_PORTFOLIO_REPLACE_ENABLED, S55_MIN_REPLACE_EDGE_PCT
    if "S55_STALE_MULTIPLIER" in os.environ:
        try:
            S55_STALE_MULTIPLIER = max(0.1, float(os.environ["S55_STALE_MULTIPLIER"]))
        except (TypeError, ValueError):
            pass
    if "S55_PORTFOLIO_REPLACE_ENABLED" in os.environ:
        S55_PORTFOLIO_REPLACE_ENABLED = _env_bool("S55_PORTFOLIO_REPLACE_ENABLED", True)
    if "S55_MIN_REPLACE_EDGE_PCT" in os.environ:
        try:
            S55_MIN_REPLACE_EDGE_PCT = float(os.environ["S55_MIN_REPLACE_EDGE_PCT"])
        except (TypeError, ValueError):
            pass


refresh_portfolio_config_from_env()


def remaining_expected_pnl_pct(conn: Any, open_row: dict[str, Any] | Any) -> float:
    """Heuristic remaining expectancy for an open trade (lower = weaker).

    Uses S55 similar-trade estimate when possible; falls back to age penalty + MFE/MAE.
    """
    from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55

    try:
        features = s55.build_entry_features(conn, open_row)
        neighbors = s55.find_similar_trades(conn, features, k=min(50, s55.S55_SIMILAR_K))
        if neighbors:
            est = s55.estimate_from_neighbors(neighbors)
            base = float(est.get("expected_pnl_pct") or 0.0)
        else:
            base = 0.0
    except Exception:
        base = 0.0

    now = int(time.time())
    age = max(0, now - int(open_row["created_at"] or now))
    # Age penalty: after 6h start decaying remaining expectancy.
    age_hours = age / 3600.0
    age_penalty = min(2.0, age_hours / 12.0)  # up to -2% after ~24h
    mfe = float(open_row.get("mfe_pct") or 0.0) if hasattr(open_row, "get") else float(open_row["mfe_pct"] or 0)
    mae = float(open_row.get("mae_pct") or 0.0) if hasattr(open_row, "get") else float(open_row["mae_pct"] or 0)
    # Already deep in MAE → weaker remaining.
    drawdown_penalty = abs(min(0.0, mae)) * 0.25
    # Already captured MFE without close → slight credit (still open = risk).
    run_credit = max(0.0, mfe) * 0.1
    return round(base - age_penalty - drawdown_penalty + run_credit, 4)


def list_open_ranked_weakest_first(conn: Any) -> list[dict[str, Any]]:
    from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
        STATUS_OPEN,
    )

    rows = conn.execute(
        """
        SELECT * FROM market_events_paper_trades_s42
        WHERE status = ?
        ORDER BY created_at ASC
        """,
        (STATUS_OPEN,),
    ).fetchall()
    scored: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["remaining_expected_pnl_pct"] = remaining_expected_pnl_pct(conn, d)
        scored.append(d)
    scored.sort(key=lambda x: (x["remaining_expected_pnl_pct"], int(x["created_at"] or 0)))
    return scored


def force_close_open(
    conn: Any,
    row: dict[str, Any] | Any,
    *,
    exit_reason: str,
    now: int | None = None,
    exit_price: float | None = None,
) -> None:
    """Close an open paper trade (entry as mark if no price)."""
    from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
        _close_trade,
        _current_price,
    )

    now = int(now if now is not None else time.time())
    price = exit_price
    if price is None:
        price = _current_price(conn, str(row["symbol"]))
    if price is None:
        price = float(row["entry"])
    _close_trade(
        conn,
        row=row,
        exit_price=float(price),
        exit_reason=exit_reason,
        now=now,
        trailing_exit_reason=exit_reason.lower() if exit_reason else None,
    )


def sweep_stale_opens(conn: Any, *, now: int | None = None) -> dict[str, int]:
    """Force-close opens past timeout (even when live price missing)."""
    from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
        STATUS_OPEN,
        TIMEOUT_SECONDS,
    )

    refresh_portfolio_config_from_env()
    now = int(now if now is not None else time.time())
    cutoff = now - int(TIMEOUT_SECONDS * S55_STALE_MULTIPLIER)
    rows = conn.execute(
        """
        SELECT * FROM market_events_paper_trades_s42
        WHERE status = ? AND created_at <= ?
        ORDER BY created_at ASC
        """,
        (STATUS_OPEN, cutoff),
    ).fetchall()
    closed = 0
    for r in rows:
        try:
            force_close_open(conn, r, exit_reason=EXIT_STALE, now=now)
            closed += 1
        except Exception as exc:
            logger.warning("s55 stale close failed id=%s: %s", r["id"], exc)
    return {"stale_closed": closed, "cutoff": cutoff}


def maybe_replace_weakest_for_candidate(
    conn: Any,
    *,
    candidate_expected_pnl_pct: float,
    open_count: int,
    max_open: int,
    now: int | None = None,
) -> dict[str, Any]:
    """If at cap, close weakest open when candidate has enough edge.

    Returns {replaced: bool, freed: int, weakest_id, weakest_remaining, edge}.
    """
    refresh_portfolio_config_from_env()
    out: dict[str, Any] = {
        "replaced": False,
        "freed": 0,
        "weakest_id": None,
        "weakest_remaining": None,
        "edge": None,
    }
    if not S55_PORTFOLIO_REPLACE_ENABLED:
        return out
    if open_count < max_open:
        return out

    ranked = list_open_ranked_weakest_first(conn)
    if not ranked:
        return out
    weakest = ranked[0]
    rem = float(weakest["remaining_expected_pnl_pct"])
    edge = float(candidate_expected_pnl_pct) - rem
    out["weakest_id"] = int(weakest["id"])
    out["weakest_remaining"] = rem
    out["edge"] = round(edge, 4)
    if edge < S55_MIN_REPLACE_EDGE_PCT:
        return out

    now = int(now if now is not None else time.time())
    force_close_open(conn, weakest, exit_reason=EXIT_PORTFOLIO_REPLACE, now=now)
    out["replaced"] = True
    out["freed"] = 1
    logger.info(
        "S55 portfolio replace: closed id=%s remaining=%.4f for candidate edge=%.4f",
        weakest["id"],
        rem,
        edge,
    )
    return out


def enforce_max_open_hard_cap(conn: Any, *, max_open: int, now: int | None = None) -> int:
    """If still over cap after stale sweep, close weakest until <= max_open."""
    refresh_portfolio_config_from_env()
    now = int(now if now is not None else time.time())
    closed = 0
    while True:
        ranked = list_open_ranked_weakest_first(conn)
        if len(ranked) <= max_open:
            break
        weakest = ranked[0]
        force_close_open(conn, weakest, exit_reason=EXIT_PORTFOLIO_REPLACE, now=now)
        closed += 1
        if closed > 5000:  # safety
            logger.error("S55 enforce_max_open_hard_cap safety stop after %s closes", closed)
            break
    return closed


__all__ = [
    "EXIT_PORTFOLIO_REPLACE",
    "EXIT_STALE",
    "S55_MIN_REPLACE_EDGE_PCT",
    "S55_PORTFOLIO_REPLACE_ENABLED",
    "enforce_max_open_hard_cap",
    "force_close_open",
    "list_open_ranked_weakest_first",
    "maybe_replace_weakest_for_candidate",
    "refresh_portfolio_config_from_env",
    "remaining_expected_pnl_pct",
    "sweep_stale_opens",
]
