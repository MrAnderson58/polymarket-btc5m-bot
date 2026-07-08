"""Deterministic source rating engine (Phase C).

Aggregates thesis outcomes into per-channel scores with shrinkage and recency weighting.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.db import connection_is_postgres, validate_write_table
from bot.research.futures_agent.research_scoring import bayesian_shrinkage_rate, wilson_lower_bound


def _profit_factor(returns: list[float]) -> float | None:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses <= 1e-12:
        return None if gains <= 1e-12 else float("inf")
    return gains / losses


def _sharpe(returns: list[float]) -> float | None:
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std <= 1e-12:
        return None
    return mean / std * math.sqrt(n)


def _recency_weighted_mean(pairs: list[tuple[float, float]], half_life_days: float = 90.0) -> float | None:
    if not pairs:
        return None
    lam = math.log(2) / half_life_days
    num = 0.0
    den = 0.0
    for value, age_days in pairs:
        w = math.exp(-lam * max(0.0, age_days))
        num += value * w
        den += w
    return num / den if den > 0 else None


def evaluate_sources(
    conn: Any,
    *,
    horizon: str = "1d",
    min_sample: int = 30,
    half_life_days: float = 90.0,
    progress_every: int = 500,
) -> dict[str, Any]:
    """Compute `futures_agent_source_scores_v2` from evaluated thesis outcomes."""
    validate_write_table("futures_agent_source_scores_v2")
    now = int(time.time())

    # Aggregate by channel/content_type/symbol/direction/timeframe.
    rows = conn.execute(
        """
        SELECT
          p.channel_name,
          p.content_type,
          t.symbol,
          t.direction,
          t.horizon AS timeframe,
          o.return_pct,
          o.mfe_pct,
          o.mae_pct,
          o.evaluated_at,
          o.direction_correct
        FROM futures_agent_thesis_outcomes o
        JOIN futures_agent_trader_theses t ON t.id = o.thesis_id
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE o.evaluation_horizon = ?
          AND o.return_pct IS NOT NULL
          AND t.symbol IS NOT NULL
        """,
        (horizon,),
    ).fetchall()

    buckets: dict[tuple[str, str, str, str, str, str], dict[str, list[float] | list[tuple[float, float]] | int]] = {}
    for r in rows:
        key = (
            str(r["channel_name"]),
            str(r["content_type"]),
            str(r["symbol"]),
            str(r["direction"]),
            str(r["timeframe"] or "unknown"),
            str(horizon),
        )
        b = buckets.setdefault(key, {"returns": [], "mfe": [], "mae": [], "rec_pairs": [], "wins": 0, "n": 0})
        ret = float(r["return_pct"])
        b["returns"].append(ret)  # type: ignore[union-attr]
        if r["mfe_pct"] is not None:
            b["mfe"].append(float(r["mfe_pct"]))  # type: ignore[union-attr]
        if r["mae_pct"] is not None:
            b["mae"].append(float(r["mae_pct"]))  # type: ignore[union-attr]
        age_days = (now - int(r["evaluated_at"])) / 86400.0 if r["evaluated_at"] else 0.0
        b["rec_pairs"].append((ret, age_days))  # type: ignore[union-attr]
        dc = r["direction_correct"]
        if dc is not None and int(dc) == 1:
            b["wins"] = int(b["wins"]) + 1  # type: ignore[index]
        b["n"] = int(b["n"]) + 1  # type: ignore[index]

    postgres = connection_is_postgres(conn)
    processed = 0
    for (channel, content_type, symbol, direction, timeframe, hz), b in buckets.items():
        processed += 1
        n = int(b["n"])  # type: ignore[index]
        if n < min_sample:
            continue
        wins = int(b["wins"])  # type: ignore[index]
        rets = b["returns"]  # type: ignore[index]
        mfes = b["mfe"]  # type: ignore[index]
        maes = b["mae"]  # type: ignore[index]
        rec_pairs = b["rec_pairs"]  # type: ignore[index]

        win_rate = wins / n if n else None
        avg_return = (sum(rets) / len(rets)) if rets else None
        avg_mfe = (sum(mfes) / len(mfes)) if mfes else None
        avg_mae = (sum(maes) / len(maes)) if maes else None
        pf = _profit_factor(rets) if rets else None
        shrp = _sharpe(rets) if rets else None
        bayes = bayesian_shrinkage_rate(wins, n, prior_rate=0.5, prior_weight=20.0)
        wlb = wilson_lower_bound(wins, n)
        rec = _recency_weighted_mean(rec_pairs, half_life_days=half_life_days)

        validate_write_table("futures_agent_source_scores_v2")
        if postgres:
            conn.execute(
                """
                INSERT INTO futures_agent_source_scores_v2 (
                  channel_name, content_type, symbol, direction, timeframe, horizon,
                  sample_size, win_rate, avg_return, avg_mfe, avg_mae,
                  profit_factor, sharpe, bayesian_mean, wilson_lower_bound,
                  recency_weighted_score, calculated_as_of
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (channel_name, content_type, symbol, direction, timeframe, horizon) DO UPDATE SET
                  sample_size = EXCLUDED.sample_size,
                  win_rate = EXCLUDED.win_rate,
                  avg_return = EXCLUDED.avg_return,
                  avg_mfe = EXCLUDED.avg_mfe,
                  avg_mae = EXCLUDED.avg_mae,
                  profit_factor = EXCLUDED.profit_factor,
                  sharpe = EXCLUDED.sharpe,
                  bayesian_mean = EXCLUDED.bayesian_mean,
                  wilson_lower_bound = EXCLUDED.wilson_lower_bound,
                  recency_weighted_score = EXCLUDED.recency_weighted_score,
                  calculated_as_of = EXCLUDED.calculated_as_of,
                  updated_at = NOW()
                """,
                (
                    channel, content_type, symbol, direction, timeframe, hz,
                    n, win_rate, avg_return, avg_mfe, avg_mae,
                    pf, shrp, bayes, wlb, rec, now,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO futures_agent_source_scores_v2 (
                  channel_name, content_type, symbol, direction, timeframe, horizon,
                  sample_size, win_rate, avg_return, avg_mfe, avg_mae,
                  profit_factor, sharpe, bayesian_mean, wilson_lower_bound,
                  recency_weighted_score, calculated_as_of
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_name, content_type, symbol, direction, timeframe, horizon) DO UPDATE SET
                  sample_size=excluded.sample_size,
                  win_rate=excluded.win_rate,
                  avg_return=excluded.avg_return,
                  avg_mfe=excluded.avg_mfe,
                  avg_mae=excluded.avg_mae,
                  profit_factor=excluded.profit_factor,
                  sharpe=excluded.sharpe,
                  bayesian_mean=excluded.bayesian_mean,
                  wilson_lower_bound=excluded.wilson_lower_bound,
                  recency_weighted_score=excluded.recency_weighted_score,
                  calculated_as_of=excluded.calculated_as_of,
                  updated_at=datetime('now')
                """,
                (
                    channel, content_type, symbol, direction, timeframe, hz,
                    n, win_rate, avg_return, avg_mfe, avg_mae,
                    pf, shrp, bayes, wlb, rec, now,
                ),
            )

        if progress_every and processed % progress_every == 0:
            print(f"Processed: {processed:,} buckets | current channel={channel} symbol={symbol}", flush=True)

    conn.commit()
    return {"buckets": len(buckets), "processed": processed}


def render_top_sources(conn: Any, *, horizon: str = "1d", limit: int = 20) -> str:
    rows = conn.execute(
        """
        SELECT channel_name, content_type, direction, timeframe, symbol,
               sample_size, win_rate, avg_return, bayesian_mean, wilson_lower_bound
        FROM futures_agent_source_scores_v2
        WHERE horizon = ?
        ORDER BY wilson_lower_bound DESC NULLS LAST, sample_size DESC
        LIMIT ?
        """,
        (horizon, limit),
    ).fetchall()
    lines = ["TOP SOURCES", ""]
    for r in rows:
        lines.append(
            f"{r['channel_name']} {r['content_type']} {r['direction']} tf={r['timeframe']} {r['symbol']} "
            f"n={r['sample_size']} win={r['win_rate']:.2f} "
            f"ret={r['avg_return']:.3f} bayes={r['bayesian_mean']:.3f} wlb={r['wilson_lower_bound']:.3f}"
        )
    return "\n".join(lines)

