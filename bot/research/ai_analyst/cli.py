"""S46/S47/S48 CLI — python -m bot.research.ai_analyst run|paper|validate ..."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bot.research.ai_analyst",
        description="S46 AI Research Analyst + S47 Paper Trading + S48 Validation",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Build context and generate report artifacts")
    run.add_argument("--morning", action="store_true", help="Morning brief only")
    run.add_argument("--evening", action="store_true", help="Evening brief only")
    run.add_argument("--btc", action="store_true", help="BTC brief only")
    run.add_argument("--macro", action="store_true", help="Macro brief only")
    run.add_argument("--sp500", action="store_true", help="S&P500 brief only")
    run.add_argument("--telegram", action="store_true", help="Telegram post only")
    run.add_argument("--x", action="store_true", help="X post only")
    run.add_argument(
        "--template",
        action="store_true",
        help="Force deterministic template LLM (no API)",
    )
    run.add_argument(
        "--no-live",
        action="store_true",
        help="Skip Yahoo/Farside live enrichment (DB-only context)",
    )
    run.add_argument("--json", action="store_true", help="Print result JSON to stdout")
    run.add_argument(
        "--dump-context",
        action="store_true",
        help="Only write market_context.json and exit",
    )

    paper = sub.add_parser("paper", help="S47 AI paper trading engine")
    paper.add_argument(
        "--demo",
        action="store_true",
        help="Run end-to-end paper demo (signal → entry → TP1/TP2/TP3)",
    )
    paper.add_argument("--stats", action="store_true", help="Print strategy stats")
    paper.add_argument("--json", action="store_true", help="JSON output")

    validate = sub.add_parser("validate", help="S48 strategy validation & ranking")
    validate.add_argument("--demo", action="store_true", help="Seed demo outcomes and report")
    validate.add_argument("--stats", action="store_true", help="Print dashboard")
    validate.add_argument("--ranking", action="store_true", help="Print AI ranking")
    validate.add_argument("--daily", action="store_true", help="Print daily report")
    validate.add_argument("--json", action="store_true", help="JSON output")
    return p


def _run_validate_demo() -> dict:
    from bot.research.ai_analyst.paper_trading.signals import TradingSignal
    from bot.research.ai_analyst.strategy_validation.ranking import format_ranking
    from bot.research.ai_analyst.strategy_validation.service import StrategyValidationService

    svc = StrategyValidationService()
    # Winning ETF+AI setup
    w = TradingSignal(
        symbol="BTC", direction="LONG",
        entry_low=100, entry_high=102, stop_loss=95,
        tp1=110, tp2=120, tp3=130,
        risk_pct=1.0, confidence=75,
        reasons=["Positive ETF inflows", "AI narrative strong", "Neutral funding"],
        strategy="etf_ai",
        signal_id="s48-win-1",
    )
    svc.submit_signal(w)
    svc.tick("BTC", 101, ts=10)
    svc.tick("BTC", 110, ts=20)
    svc.tick("BTC", 120, ts=30)
    svc.tick("BTC", 130, ts=40)

    # Losing Macro+High Funding
    l = TradingSignal(
        symbol="BTC", direction="LONG",
        entry_low=200, entry_high=202, stop_loss=190,
        tp1=210, tp2=220, tp3=230,
        risk_pct=1.0, confidence=55,
        reasons=["Macro headwinds", "High funding crowding"],
        strategy="macro_funding",
        signal_id="s48-loss-1",
    )
    svc.submit_signal(l)
    svc.tick("BTC", 201, ts=50)
    svc.tick("BTC", 190, ts=60)

    ranking = svc.ranking()
    return {
        "ok": True,
        "stats": svc.dashboard(),
        "ranking": ranking,
        "ranking_text": format_ranking(ranking),
        "daily": svc.daily_report(now=70),
        "signals": svc.format_signals(),
        "closed": svc.format_closed(),
        "leaderboard": svc.format_leaderboard(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "paper":
        from bot.research.ai_analyst.paper_trading.runner import run_paper_demo

        result = run_paper_demo()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(result.get("report") or "")
            st = result.get("stats") or {}
            print(
                f"ok={result.get('ok')} events={len(result.get('events') or [])} "
                f"WinRate={st.get('win_rate')}% PF={st.get('profit_factor')} "
                f"Expectancy={st.get('expectancy')}R"
            )
        return 0 if result.get("ok") else 1

    if args.command == "validate":
        result = _run_validate_demo()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if args.ranking:
                print(result["ranking_text"])
            elif args.daily:
                print(result["daily"])
            elif args.stats:
                from bot.research.ai_analyst.strategy_validation.dashboard import format_dashboard
                print(format_dashboard(result["stats"]))
            else:
                print(result["daily"])
                print()
                print(result["ranking_text"])
                print()
                from bot.research.ai_analyst.strategy_validation.dashboard import format_dashboard
                print(format_dashboard(result["stats"]))
        return 0 if result.get("ok") else 1

    if args.command != "run":
        parser.print_help()
        return 2

    from bot.research.ai_analyst.context_builder import build_market_context, dump_context_json
    from bot.research.ai_analyst.config import REPORTS_DIR
    from bot.research.ai_analyst.report_generator import run_ai_analyst

    if args.dump_context:
        ctx = build_market_context()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORTS_DIR / "market_context.json"
        path.write_text(dump_context_json(ctx) + "\n", encoding="utf-8")
        print(str(path))
        return 0

    flags: list[str] = []
    for name in ("morning", "evening", "btc", "macro", "sp500", "telegram", "x"):
        if getattr(args, name, False):
            flags.append(name)

    result = run_ai_analyst(
        flags=flags or None,
        force_template=bool(args.template),
        live_enrich=not bool(args.no_live),
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"provider={result.get('provider')} model={result.get('model')}")
        print(f"context={result.get('context_path')}")
        for art in result.get("artifacts") or []:
            err = f" err={art.get('error')}" if art.get("error") else ""
            print(
                f"- {art.get('agent_id')}: {art.get('path')} "
                f"({art.get('chars')} chars, {art.get('latency_ms')}ms){err}"
            )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
