"""Deterministic thesis outcome evaluation (Phase C).

No LLM, no external APIs. Uses only source DB market_prices after thesis message_ts.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Iterable

from bot.research.futures_agent.db import connection_is_postgres, validate_write_table
from bot.research.futures_agent.market_prices import iter_market_prices
from bot.research.futures_agent.source_requirements import open_stage3_source_reader


DEFAULT_HORIZONS: dict[str, int] = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
}


@dataclass(frozen=True)
class OutcomeResult:
    thesis_id: int
    horizon: str
    price_at_thesis: float | None
    mfe_pct: float | None
    mae_pct: float | None
    return_pct: float | None
    direction_correct: int | None
    target_hit: int | None
    stop_hit: int | None
    time_to_target_sec: int | None
    time_to_stop_sec: int | None
    final_outcome: str | None
    evaluated_at: int


def _pct_change(entry: float, px: float) -> float:
    if entry <= 0:
        return 0.0
    return px / entry - 1.0


def _directional_return(direction: str, entry: float, px: float) -> float:
    if direction == "SHORT":
        # favorable when price falls
        return (entry / px - 1.0) if px > 0 else 0.0
    return _pct_change(entry, px)


def _hit_level(direction: str, level_type: str, level_price: float, px: float) -> bool:
    if direction == "SHORT":
        if level_type == "TARGET":
            return px <= level_price
        if level_type == "STOP":
            return px >= level_price
        return False
    if level_type == "TARGET":
        return px >= level_price
    if level_type == "STOP":
        return px <= level_price
    return False


def _choose_entry_price(price_at_thesis: float | None, entry_low: float | None, entry_high: float | None) -> float | None:
    if entry_low is not None and entry_high is not None:
        return (entry_low + entry_high) / 2.0
    if entry_low is not None:
        return float(entry_low)
    if entry_high is not None:
        return float(entry_high)
    return float(price_at_thesis) if price_at_thesis is not None else None


def _iter_theses_for_eval(
    conn: Any,
    *,
    limit: int | None = None,
    channel: str | None = None,
    symbol: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> Iterable[dict[str, Any]]:
    q = """
    SELECT
      t.id AS thesis_id,
      p.channel_name,
      p.content_type,
      p.message_ts,
      t.symbol,
      t.direction,
      t.horizon,
      t.confidence
    FROM futures_agent_trader_theses t
    JOIN futures_agent_trader_posts p ON p.id = t.post_id
    WHERE t.symbol IS NOT NULL
    """
    params: list[Any] = []
    if channel:
        q += " AND p.channel_name = ?"
        params.append(channel)
    if symbol:
        q += " AND t.symbol = ?"
        params.append(symbol)
    if start_ts is not None:
        q += " AND p.message_ts >= ?"
        params.append(int(start_ts))
    if end_ts is not None:
        q += " AND p.message_ts <= ?"
        params.append(int(end_ts))
    q += " ORDER BY p.message_ts ASC"
    if limit is not None:
        q += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(q, params).fetchall()


def _load_levels(conn: Any, thesis_id: int) -> dict[str, list[float]]:
    rows = conn.execute(
        "SELECT level_type, price FROM futures_agent_trader_levels WHERE thesis_id = ? ORDER BY ordinal ASC",
        (thesis_id,),
    ).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(str(r["level_type"]), []).append(float(r["price"]))
    return out


def _upsert_outcome(conn: Any, res: OutcomeResult) -> None:
    validate_write_table("futures_agent_thesis_outcomes")
    postgres = connection_is_postgres(conn)
    if postgres:
        conn.execute(
            """
            INSERT INTO futures_agent_thesis_outcomes (
              thesis_id, evaluation_horizon, price_at_thesis,
              mfe_pct, mae_pct, return_pct, direction_correct,
              target_hit, stop_hit, time_to_target_sec, time_to_stop_sec,
              final_outcome, evaluated_at
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (thesis_id, evaluation_horizon) DO UPDATE SET
              price_at_thesis = EXCLUDED.price_at_thesis,
              mfe_pct = EXCLUDED.mfe_pct,
              mae_pct = EXCLUDED.mae_pct,
              return_pct = EXCLUDED.return_pct,
              direction_correct = EXCLUDED.direction_correct,
              target_hit = EXCLUDED.target_hit,
              stop_hit = EXCLUDED.stop_hit,
              time_to_target_sec = EXCLUDED.time_to_target_sec,
              time_to_stop_sec = EXCLUDED.time_to_stop_sec,
              final_outcome = EXCLUDED.final_outcome,
              evaluated_at = EXCLUDED.evaluated_at
            """,
            (
                res.thesis_id, res.horizon, res.price_at_thesis,
                res.mfe_pct, res.mae_pct, res.return_pct, res.direction_correct,
                res.target_hit, res.stop_hit, res.time_to_target_sec, res.time_to_stop_sec,
                res.final_outcome, res.evaluated_at,
            ),
        )
        return

    conn.execute(
        """
        INSERT INTO futures_agent_thesis_outcomes (
          thesis_id, evaluation_horizon, price_at_thesis,
          mfe_pct, mae_pct, return_pct, direction_correct,
          target_hit, stop_hit, time_to_target_sec, time_to_stop_sec,
          final_outcome, evaluated_at
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(thesis_id, evaluation_horizon) DO UPDATE SET
          price_at_thesis=excluded.price_at_thesis,
          mfe_pct=excluded.mfe_pct,
          mae_pct=excluded.mae_pct,
          return_pct=excluded.return_pct,
          direction_correct=excluded.direction_correct,
          target_hit=excluded.target_hit,
          stop_hit=excluded.stop_hit,
          time_to_target_sec=excluded.time_to_target_sec,
          time_to_stop_sec=excluded.time_to_stop_sec,
          final_outcome=excluded.final_outcome,
          evaluated_at=excluded.evaluated_at
        """,
        (
            res.thesis_id, res.horizon, res.price_at_thesis,
            res.mfe_pct, res.mae_pct, res.return_pct, res.direction_correct,
            res.target_hit, res.stop_hit, res.time_to_target_sec, res.time_to_stop_sec,
            res.final_outcome, res.evaluated_at,
        ),
    )


def evaluate_theses(
    conn: Any,
    *,
    horizons: dict[str, int] | None = None,
    limit: int | None = None,
    channel: str | None = None,
    symbol: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    progress_every: int = 1000,
) -> dict[str, Any]:
    """Evaluate thesis outcomes and upsert into futures_agent_thesis_outcomes."""
    hz = horizons or DEFAULT_HORIZONS
    theses = list(_iter_theses_for_eval(conn, limit=limit, channel=channel, symbol=symbol, start_ts=start_ts, end_ts=end_ts))
    total = len(theses)
    processed = 0
    t0 = time.time()

    source = open_stage3_source_reader()
    try:
        for th in theses:
            processed += 1
            th_id = int(th["thesis_id"])
            th_symbol = str(th["symbol"])
            th_dir = str(th["direction"])
            msg_ts = int(th["message_ts"])

            levels = _load_levels(conn, th_id)
            entry_low = levels.get("ENTRY_LOW", [None])[0] if levels.get("ENTRY_LOW") else None
            entry_high = levels.get("ENTRY_HIGH", [None])[0] if levels.get("ENTRY_HIGH") else None
            stop = levels.get("STOP", [None])[0] if levels.get("STOP") else None
            targets = levels.get("TARGET", [])[:3]
            target_price = min(targets) if (targets and th_dir == "SHORT") else (max(targets) if targets else None)

            for horizon_name, seconds in hz.items():
                end_eval = msg_ts + seconds
                series = list(iter_market_prices(source, symbol=th_symbol, start_ts=msg_ts, end_ts=end_eval))
                if not series:
                    res = OutcomeResult(
                        thesis_id=th_id,
                        horizon=horizon_name,
                        price_at_thesis=None,
                        mfe_pct=None,
                        mae_pct=None,
                        return_pct=None,
                        direction_correct=None,
                        target_hit=None,
                        stop_hit=None,
                        time_to_target_sec=None,
                        time_to_stop_sec=None,
                        final_outcome="NO_PRICE_DATA",
                        evaluated_at=int(time.time()),
                    )
                    _upsert_outcome(conn, res)
                    continue

                entry_price = float(series[0]["price"])
                chosen_entry = _choose_entry_price(entry_price, entry_low, entry_high) or entry_price

                mfe = -math.inf
                mae = math.inf
                final_px = float(series[-1]["price"])

                target_hit = 0
                stop_hit = 0
                t_to_tp: int | None = None
                t_to_sl: int | None = None

                for pt in series:
                    px = float(pt["price"])
                    ts = int(pt["ts"])
                    dret = _directional_return(th_dir, chosen_entry, px)
                    mfe = max(mfe, dret)
                    mae = min(mae, dret)

                    if target_price is not None and not target_hit and _hit_level(th_dir, "TARGET", float(target_price), px):
                        target_hit = 1
                        t_to_tp = max(0, ts - msg_ts)
                    if stop is not None and not stop_hit and _hit_level(th_dir, "STOP", float(stop), px):
                        stop_hit = 1
                        t_to_sl = max(0, ts - msg_ts)
                    if target_hit and stop_hit:
                        # keep earliest times only
                        pass

                ret = _directional_return(th_dir, chosen_entry, final_px)
                direction_correct = 1 if ret > 0 else (0 if ret < 0 else None)

                final_outcome = "OPEN"
                if target_hit and stop_hit:
                    # whichever first
                    if t_to_tp is not None and t_to_sl is not None:
                        final_outcome = "TP_FIRST" if t_to_tp <= t_to_sl else "SL_FIRST"
                elif target_hit:
                    final_outcome = "TP"
                elif stop_hit:
                    final_outcome = "SL"
                else:
                    final_outcome = "WIN" if ret > 0 else ("LOSS" if ret < 0 else "FLAT")

                res = OutcomeResult(
                    thesis_id=th_id,
                    horizon=horizon_name,
                    price_at_thesis=float(entry_price),
                    mfe_pct=float(mfe) if mfe != -math.inf else None,
                    mae_pct=float(mae) if mae != math.inf else None,
                    return_pct=float(ret),
                    direction_correct=direction_correct,
                    target_hit=target_hit if target_price is not None else None,
                    stop_hit=stop_hit if stop is not None else None,
                    time_to_target_sec=t_to_tp if target_price is not None else None,
                    time_to_stop_sec=t_to_sl if stop is not None else None,
                    final_outcome=final_outcome,
                    evaluated_at=int(time.time()),
                )
                _upsert_outcome(conn, res)

            if progress_every and processed % progress_every == 0:
                dt = max(1e-6, time.time() - t0)
                speed = processed / dt
                eta = (total - processed) / speed if speed > 0 else 0.0
                ch = str(th["channel_name"])
                print(
                    f"Processed: {processed:,} / {total:,} | channel={ch} | symbol={th_symbol} "
                    f"| speed={speed:.1f}/s | ETA={eta/60:.1f}m",
                    flush=True,
                )
        conn.commit()
    finally:
        source.close()

    return {"theses": total, "processed": processed}

