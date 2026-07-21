"""S47 CLI helpers for paper trading engine."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.ai_analyst.paper_trading.engine import PaperTradingEngine
from bot.research.ai_analyst.paper_trading.reports import format_full_report
from bot.research.ai_analyst.paper_trading.signal_builder import build_signal_from_context
from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.paper_trading.store import load_engine, persist_engine


def run_paper_demo(*, force_template: bool = True) -> dict[str, Any]:
    """
    End-to-end demo: build context → signal → tick through entry/TP path.
    Uses live context when available; falls back to synthetic BTC path.
    """
    from bot.research.ai_analyst.context_builder import build_market_context
    from bot.research.ai_analyst.reasoning_engine import enrich_context_for_analysis

    try:
        ctx = enrich_context_for_analysis(build_market_context(live_enrich=True))
    except Exception:
        ctx = {"btc": {"price": 100_000.0, "trend": "Bullish", "change_24h_pct": 1.0}}

    engine = PaperTradingEngine(initial_equity=10_000.0)
    sig = build_signal_from_context(ctx, strategy="ai_context")
    if sig is None:
        # Synthetic long for offline demo
        px = float((ctx.get("btc") or {}).get("price") or 100_000)
        sig = TradingSignal(
            symbol="BTC",
            direction="LONG",
            entry_low=px * 0.999,
            entry_high=px * 1.001,
            stop_loss=px * 0.99,
            tp1=px * 1.01,
            tp2=px * 1.02,
            tp3=px * 1.03,
            risk_pct=1.0,
            confidence=60.0,
            reasons=["Synthetic demo signal"],
            strategy="ai_demo",
        )
    engine.submit_signal(sig)

    mid = sig.entry_mid()
    now = int(time.time())
    events = []
    events += engine.tick(sig.symbol, mid, ts=now)
    events += engine.tick(sig.symbol, sig.tp1, ts=now + 60)
    events += engine.tick(sig.symbol, sig.tp2, ts=now + 120)
    events += engine.tick(sig.symbol, sig.tp3, ts=now + 180)

    report = format_full_report(engine)
    return {
        "ok": True,
        "signal": sig.to_dict(),
        "events": events,
        "stats": engine.stats(),
        "report": report,
        "snapshot": engine.snapshot(),
        "template": force_template,
    }


def submit_and_persist_signal(
    conn: Any,
    signal: TradingSignal,
    *,
    engine: PaperTradingEngine | None = None,
) -> PaperTradingEngine:
    eng = engine or load_engine(conn)
    eng.submit_signal(signal)
    persist_engine(conn, eng, now=int(time.time()))
    return eng
