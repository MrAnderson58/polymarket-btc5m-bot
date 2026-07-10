"""TradFi/core collector path verification audit."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import PRICE_HISTORY_SEC
from bot.research.market_events.detector_diagnostics import (
    REJECTION_FIRED,
    diagnose_universe,
)
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.paper_runner import ShockPaperRunner
from bot.research.market_events.universe import needs_multi_venue_feed, select_universe


@dataclass
class PathAuditResult:
    universe: str
    duration_sec: int
    instruments_loaded: list[str] = field(default_factory=list)
    quote_polls_ok: int = 0
    quote_polls_failed: int = 0
    symbols_with_ts_movement: set[str] = field(default_factory=set)
    symbols_with_price_movement: set[str] = field(default_factory=set)
    rolling_window_ok: dict[str, dict[int, bool]] = field(default_factory=lambda: defaultdict(dict))
    detector_evaluations: int = 0
    rejection_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    lifecycle_creations: int = 0
    db_writes: int = 0
    per_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)


def run_collector_path_audit(
    conn: Any,
    *,
    universe: str = "tradfi-liquid",
    seconds: int = 30,
    explicit_symbols: list[str] | None = None,
) -> str:
    apply_migrations(conn)
    symbols, version = select_universe(conn, mode=universe, explicit_symbols=explicit_symbols)
    runner = ShockPaperRunner(
        universe_mode=universe,
        paper_only=True,
        max_cycles=0,
        explicit_symbols=explicit_symbols,
        heartbeat_sec=999999,
    )

    if needs_multi_venue_feed(universe, explicit_symbols):
        from bot.research.market_events.instrument_master import load_paper_instruments
        from bot.research.market_events.multi_venue_feed import MultiVenuePriceFeed
        if universe == "tradfi-liquid":
            instruments = [dict(r) for r in load_paper_instruments(conn, tradfi_only=True)]
        else:
            instruments = [dict(r) for r in load_paper_instruments(conn)]
        runner.feed = MultiVenuePriceFeed(instruments)
        runner._instrument_map = {r["canonical_asset"]: r for r in instruments}

    result = PathAuditResult(universe=universe, duration_sec=seconds)
    result.instruments_loaded = list(symbols)

    prev_prices: dict[str, float | None] = {s: None for s in symbols}
    prev_ts: dict[str, int | None] = {s: None for s in symbols}
    events_before = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
    pending_before = conn.execute("SELECT COUNT(*) FROM market_events_pending_shocks").fetchone()[0]

    start = time.time()
    cycles = 0
    while time.time() - start < seconds:
        cycle_start = time.perf_counter()
        now = int(time.time())
        poll_out = runner.feed.poll_universe(symbols, max_age_sec=PRICE_HISTORY_SEC)
        result.quote_polls_ok += len(poll_out)
        result.quote_polls_failed += len(symbols) - len(poll_out)

        for sym in symbols:
            st = runner.feed.get_state(sym)
            if not st:
                result.per_symbol.setdefault(sym, {})["state"] = "missing"
                continue
            info = result.per_symbol.setdefault(sym, {
                "ticks": len(st.ticks),
                "last_price": st.last_price,
                "venue": runner._instrument_map.get(sym, {}).get("venue", "binance_futures"),
            })
            info["ticks"] = len(st.ticks)
            info["last_price"] = st.last_price
            if st.last_price is not None and prev_prices[sym] is not None:
                if st.last_price != prev_prices[sym]:
                    result.symbols_with_price_movement.add(sym)
            if st.ticks:
                latest_ts = st.ticks[-1].ts
                if prev_ts[sym] is not None and latest_ts != prev_ts[sym]:
                    result.symbols_with_ts_movement.add(sym)
                prev_ts[sym] = latest_ts
            prev_prices[sym] = st.last_price

            for window in (30, 60, 180):
                ret = st.return_over(window, now)
                ok = ret is not None and len(st.ticks) >= 2
                result.rolling_window_ok[sym][window] = ok

        diag = diagnose_universe(
            runner.feed, symbols, now_ts=now,
            shock_allowed_fn=runner._shock_allowed if runner._instrument_map else None,
        )
        result.detector_evaluations += diag.total_evaluations()
        for det_reasons in diag.counts.values():
            for reason, n in det_reasons.items():
                result.rejection_counts[reason] += n

        cycles += 1
        time.sleep(1.0)

    events_after = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
    pending_after = conn.execute("SELECT COUNT(*) FROM market_events_pending_shocks").fetchone()[0]
    result.lifecycle_creations = pending_after - pending_before
    result.db_writes = events_after - events_before

    lines = [
        "COLLECTOR PATH AUDIT",
        f"universe={universe} version={version}",
        f"duration_sec={seconds} cycles={cycles}",
        "",
        f"instruments_loaded={len(result.instruments_loaded)}: {','.join(result.instruments_loaded)}",
        f"quote_polls_ok={result.quote_polls_ok}",
        f"quote_polls_failed={result.quote_polls_failed}",
        f"symbols_with_timestamp_movement={len(result.symbols_with_ts_movement)} "
        f"({','.join(sorted(result.symbols_with_ts_movement)) or 'none'})",
        f"symbols_with_price_movement={len(result.symbols_with_price_movement)} "
        f"({','.join(sorted(result.symbols_with_price_movement)) or 'none'})",
        "",
        "rolling_window_population:",
    ]
    for sym in sorted(result.instruments_loaded):
        windows = result.rolling_window_ok.get(sym, {})
        parts = ", ".join(f"{w}s={'ok' if windows.get(w) else 'insufficient'}" for w in (30, 60, 180))
        info = result.per_symbol.get(sym, {})
        lines.append(
            f"  {sym} venue={info.get('venue', '?')} ticks={info.get('ticks', 0)} "
            f"last_price={info.get('last_price')} {parts}",
        )

    lines.extend([
        "",
        f"detector_evaluations={result.detector_evaluations}",
        "rejection_counts:",
    ])
    for reason, n in sorted(result.rejection_counts.items()):
        lines.append(f"  {reason}={n}")
    fired = result.rejection_counts.get(REJECTION_FIRED, 0)
    lines.append(f"detector_fired_total={fired}")
    lines.extend([
        "",
        f"lifecycle_creations={result.lifecycle_creations}",
        f"db_event_writes={result.db_writes}",
        "",
        "Purpose: verify instruments reach detector evaluation and are not silently excluded.",
    ])
    return "\n".join(lines)
