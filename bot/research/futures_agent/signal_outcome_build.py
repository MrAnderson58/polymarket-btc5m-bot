"""Build and persist Phase D.1 historical signal outcomes."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.db import connection_is_postgres, insert_returning_id, validate_write_table
from bot.research.futures_agent.historical_candles import (
    CachedCandleProvider,
    required_end_ts,
    resolve_exchange_symbol,
)
from bot.research.futures_agent.signal_outcome_constants import ENGINE_VERSION
from bot.research.futures_agent.signal_outcome_exit_policies import evaluate_all_policies
from bot.research.futures_agent.signal_outcome_path import PathEvaluation, evaluate_signal_path


@dataclass
class OutcomeBuildStats:
    signals_total: int = 0
    processed: int = 0
    outcomes_inserted: int = 0
    outcomes_skipped: int = 0
    events_inserted: int = 0
    markouts_inserted: int = 0
    symbols_unresolved: int = 0
    no_data: int = 0
    entered: int = 0
    not_entered: int = 0
    ambiguous: int = 0
    engine_version: str = ENGINE_VERSION


def _load_levels(conn: Any, thesis_id: int) -> dict[str, list[float]]:
    rows = conn.execute(
        """
        SELECT level_type, price, ordinal
        FROM futures_agent_trader_levels
        WHERE thesis_id = ?
        ORDER BY ordinal ASC
        """,
        (thesis_id,),
    ).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(str(r["level_type"]), []).append(float(r["price"]))
    return out


def _iter_explicit_signals(
    conn: Any,
    *,
    channel: str = "signalyp",
    symbol: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    engine_version: str = ENGINE_VERSION,
    only_missing: bool = True,
) -> list[dict[str, Any]]:
    q = """
    SELECT
      t.id AS thesis_id,
      t.post_id,
      p.channel_name,
      p.message_ts,
      p.raw_text,
      t.symbol,
      t.direction,
      t.horizon
  FROM futures_agent_trader_theses t
  JOIN futures_agent_trader_posts p ON p.id = t.post_id
  WHERE p.content_type = 'EXPLICIT_SIGNAL'
    AND p.channel_name = ?
    AND t.symbol IS NOT NULL
    AND t.direction IN ('LONG', 'SHORT')
    """
    params: list[Any] = [channel]
    if only_missing:
        q += """
    AND NOT EXISTS (
      SELECT 1 FROM futures_agent_research_signal_outcomes o
      WHERE o.thesis_id = t.id AND o.engine_version = ?
    )
        """
        params.append(engine_version)
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
    return conn.execute(q, params).fetchall()


def _outcome_exists(conn: Any, thesis_id: int, engine_version: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM futures_agent_research_signal_outcomes
        WHERE thesis_id = ? AND engine_version = ?
        """,
        (thesis_id, engine_version),
    ).fetchone()
    return row is not None


def _persist_outcome(
    conn: Any,
    *,
    row: dict[str, Any],
    exchange_symbol: str | None,
    symbol_status: str,
    candle_meta: dict[str, Any],
    ev: PathEvaluation,
    engine_version: str,
) -> tuple[int, int, int]:
    validate_write_table("futures_agent_research_signal_outcomes")
    policy_results = evaluate_all_policies(ev)
    policy_json = {
        pr.policy_id: {
            "return_pct": pr.return_pct,
            "exit_event": pr.exit_event,
            "notes": pr.notes,
        }
        for pr in policy_results
    }
    now = int(time.time())
    outcome_id = insert_returning_id(
        conn,
        """
        INSERT INTO futures_agent_research_signal_outcomes (
          thesis_id, post_id, channel, symbol, exchange_symbol, direction,
          decision_ts, entry_mode, entry_status, entry_ts, entry_price, entry_fill_model,
          stop_mode, stop_price, outcome_status, first_terminal_event, first_terminal_ts,
          max_target_reached, mfe_pct, mae_pct, ambiguous_intrabar,
          conservative_terminal, optimistic_terminal, raw_return_pct,
          data_quality_status, symbol_resolve_status, candle_meta_json,
          policy_results_json, engine_version, created_at
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            row["thesis_id"], row["post_id"], row["channel_name"], row["symbol"],
            exchange_symbol, row["direction"], int(row["message_ts"]),
            ev.entry_mode, ev.entry_status, ev.entry_ts, ev.entry_price, ev.entry_fill_model,
            ev.stop_mode, ev.stop_price, ev.outcome_status, ev.first_terminal_event,
            ev.first_terminal_ts, ev.max_target_reached, ev.mfe_pct, ev.mae_pct,
            ev.ambiguous_intrabar, ev.conservative_terminal, ev.optimistic_terminal,
            ev.raw_return_pct, ev.data_quality_status, symbol_status,
            json.dumps({**candle_meta, "targets": [
                {"ordinal": t.ordinal, "price": t.price, "validity": t.validity}
                for t in ev.targets
            ]}),
            json.dumps(policy_json), engine_version, now,
        ),
    )
    events_n = 0
    for i, event in enumerate(ev.events):
        conn.execute(
            """
            INSERT INTO futures_agent_research_signal_events (
              outcome_id, event_type, target_index, event_ts, event_price,
              candle_open_ts, ambiguity_flag, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome_id, event.event_type, event.target_index,
                event.event_ts, event.event_price, event.candle_open_ts,
                event.ambiguity_flag, i,
            ),
        )
        events_n += 1
    markouts_n = 0
    for m in ev.markouts:
        conn.execute(
            """
            INSERT INTO futures_agent_research_signal_markouts (
              outcome_id, horizon, horizon_seconds, mark_ts, mark_price,
              directional_return_pct, mfe_pct, mae_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome_id, m.horizon, m.horizon_seconds, m.mark_ts, m.mark_price,
                m.directional_return_pct, m.mfe_pct, m.mae_pct,
            ),
        )
        markouts_n += 1
    return outcome_id, events_n, markouts_n


def build_signal_outcomes(
    conn: Any,
    *,
    channel: str = "signalyp",
    symbol: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    engine_version: str = ENGINE_VERSION,
    progress_every: int = 50,
    candle_provider: Any | None = None,
) -> OutcomeBuildStats:
    stats = OutcomeBuildStats(engine_version=engine_version)
    rows = _iter_explicit_signals(
        conn, channel=channel, symbol=symbol,
        start_ts=start_ts, end_ts=end_ts, engine_version=engine_version,
    )
    stats.signals_total = len(rows)
    provider = candle_provider or CachedCandleProvider(conn)
    t0 = time.monotonic()

    for row in rows:
        stats.processed += 1
        thesis_id = int(row["thesis_id"])
        if _outcome_exists(conn, thesis_id, engine_version):
            stats.outcomes_skipped += 1
            continue

        exchange_symbol, _, symbol_status = resolve_exchange_symbol(row["symbol"])
        if exchange_symbol is None:
            stats.symbols_unresolved += 1
            ev = PathEvaluation(
                decision_ts=int(row["message_ts"]),
                direction=str(row["direction"]),
                entry_mode="NO_VALID_ENTRY",
                entry_status="SYMBOL_UNRESOLVED",
                entry_ts=None,
                entry_price=None,
                entry_fill_model="none",
                stop_mode="MISSING_STOP",
                stop_price=None,
                targets=[],
                outcome_status="SYMBOL_UNRESOLVED",
                data_quality_status="NO_SYMBOL",
            )
            _, ev_n, mk_n = _persist_outcome(
                conn, row=row, exchange_symbol=None, symbol_status=symbol_status,
                candle_meta={}, ev=ev, engine_version=engine_version,
            )
            stats.outcomes_inserted += 1
            stats.events_inserted += ev_n
            stats.markouts_inserted += mk_n
            continue

        decision_ts = int(row["message_ts"])
        end_fetch = required_end_ts(decision_ts)
        candles, meta = provider.fetch_range(exchange_symbol, decision_ts, end_fetch)
        dq = "COMPLETE"
        if not candles:
            dq = "NO_DATA"
            stats.no_data += 1
        elif meta.get("gap_count", 0) > 0:
            dq = "PARTIAL_GAPS"

        levels = _load_levels(conn, thesis_id)
        ev = evaluate_signal_path(
            decision_ts=decision_ts,
            direction=str(row["direction"]),
            levels=levels,
            raw_text=str(row["raw_text"] or ""),
            candles=candles,
            data_quality=dq,
        )

        if ev.entry_status == "ENTERED":
            stats.entered += 1
        elif ev.entry_status == "NOT_ENTERED":
            stats.not_entered += 1
        if ev.ambiguous_intrabar:
            stats.ambiguous += 1

        _, ev_n, mk_n = _persist_outcome(
            conn, row=row, exchange_symbol=exchange_symbol,
            symbol_status=symbol_status, candle_meta=meta, ev=ev,
            engine_version=engine_version,
        )
        stats.outcomes_inserted += 1
        stats.events_inserted += ev_n
        stats.markouts_inserted += mk_n

        if progress_every and stats.processed % progress_every == 0:
            elapsed = max(time.monotonic() - t0, 0.001)
            print(
                f"Outcome build: {stats.processed}/{stats.signals_total} "
                f"inserted={stats.outcomes_inserted} entered={stats.entered} "
                f"speed={stats.processed/elapsed:.1f}/s",
                flush=True,
            )
        if stats.processed % 25 == 0:
            conn.commit()

    conn.commit()
    return stats


def render_build_reconciliation(stats: OutcomeBuildStats) -> str:
    return "\n".join([
        "OUTCOME BUILD RECONCILIATION",
        f"engine_version: {stats.engine_version}",
        f"signals_total: {stats.signals_total:,}",
        f"processed: {stats.processed:,}",
        f"outcomes_inserted: {stats.outcomes_inserted:,}",
        f"outcomes_skipped: {stats.outcomes_skipped:,}",
        f"events_inserted: {stats.events_inserted:,}",
        f"markouts_inserted: {stats.markouts_inserted:,}",
        f"entered: {stats.entered:,}",
        f"not_entered: {stats.not_entered:,}",
        f"ambiguous_intrabar: {stats.ambiguous:,}",
        f"symbols_unresolved: {stats.symbols_unresolved:,}",
        f"no_data: {stats.no_data:,}",
    ])
