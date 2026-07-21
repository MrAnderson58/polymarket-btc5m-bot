"""S47 — paper trading reports (open / closed / strategy stats)."""

from __future__ import annotations

from typing import Any

from bot.research.ai_analyst.paper_trading.engine import PaperTradingEngine
from bot.research.ai_analyst.paper_trading.models import STATUS_CLOSED, STATUS_OPEN


def format_open_trades(engine: PaperTradingEngine) -> str:
    rows = engine.open_trades()
    lines = ["OPEN TRADES", f"count={len(rows)}", ""]
    if not rows:
        lines.append("(none)")
        return "\n".join(lines)
    for t in rows:
        lines.extend([
            f"• {t.trade_id}  {t.symbol} {t.direction} [{t.strategy}]",
            f"  Entry={t.entry}  SL={t.stop_loss}  TP1={t.tp1} TP2={t.tp2} TP3={t.tp3}",
            f"  Remaining={t.size_remaining:.2f}  MFE={t.mfe_pct:.3f}%  MAE={t.mae_pct:.3f}%",
            f"  Conf={t.confidence}%  Risk={t.risk_pct}%  Hold={t.holding_seconds}s",
            f"  Reasons: {'; '.join(t.reasons) or '—'}",
            "",
        ])
    return "\n".join(lines).rstrip()


def format_closed_trades(engine: PaperTradingEngine, *, limit: int = 20) -> str:
    rows = sorted(
        engine.closed_trades(),
        key=lambda t: int(t.closed_at or 0),
        reverse=True,
    )[:limit]
    lines = ["CLOSED TRADES", f"showing={len(rows)}", ""]
    if not rows:
        lines.append("(none)")
        return "\n".join(lines)
    for t in rows:
        lines.extend([
            f"• {t.trade_id}  {t.symbol} {t.direction} [{t.strategy}]",
            f"  Exit={t.exit_reason}@{t.exit_price}  PnL=${t.pnl_usd:.2f}  R={t.r_multiple:.2f}",
            f"  Hold={t.holding_seconds}s  MFE={t.mfe_pct:.3f}%  MAE={t.mae_pct:.3f}%",
            f"  Fills: {', '.join(f'{f.level}@{f.price}' for f in t.fills)}",
            "",
        ])
    return "\n".join(lines).rstrip()


def format_strategy_stats(engine: PaperTradingEngine) -> str:
    by_s = engine.stats_by_strategy()
    overall = engine.stats()
    lines = [
        "STRATEGY STATS",
        "",
        _fmt_stats_block("ALL", overall),
        "",
    ]
    if not by_s:
        lines.append("(no strategy breakdown yet)")
    for name, st in by_s.items():
        lines.append(_fmt_stats_block(name, st))
        lines.append("")
    return "\n".join(lines).rstrip()


def _fmt_stats_block(name: str, st: dict[str, Any]) -> str:
    pf = st.get("profit_factor")
    pf_s = "inf" if pf is None and (st.get("profit_factor_raw") == float("inf")) else (
        f"{pf:.3f}" if isinstance(pf, (int, float)) else str(pf)
    )
    return "\n".join([
        f"[{name}]",
        f"  Trades={st.get('trades', 0)}  Open={st.get('open_trades', 0)}",
        f"  WinRate={st.get('win_rate', 0)}%  W/L/BE={st.get('wins', 0)}/{st.get('losses', 0)}/{st.get('breakeven', 0)}",
        f"  ProfitFactor={pf_s}  Expectancy={st.get('expectancy', 0)}R  AvgR={st.get('avg_r', 0)}",
        f"  TotalPnL=${st.get('total_pnl_usd', 0)}  Equity=${st.get('equity', 0)}",
        f"  AvgHold={st.get('avg_hold_seconds', 0)}s  AvgMFE={st.get('avg_mfe_pct', 0)}%  AvgMAE={st.get('avg_mae_pct', 0)}%",
    ])


def format_full_report(engine: PaperTradingEngine) -> str:
    return "\n\n".join([
        format_open_trades(engine),
        format_closed_trades(engine),
        format_strategy_stats(engine),
    ])


def dashboard_dict(engine: PaperTradingEngine) -> dict[str, Any]:
    return {
        "open": [t.to_dict() for t in engine.open_trades()],
        "closed": [t.to_dict() for t in engine.closed_trades()],
        "stats": engine.stats(),
        "stats_by_strategy": engine.stats_by_strategy(),
        "equity": engine.equity,
        "counts": {
            "open": sum(1 for t in engine.trades.values() if t.status == STATUS_OPEN),
            "closed": sum(1 for t in engine.trades.values() if t.status == STATUS_CLOSED),
            "pending_signals": len(engine.pending_signals()),
        },
    }
