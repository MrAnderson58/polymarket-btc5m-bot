"""S46/S47 CLI — python -m bot.research.ai_analyst run|paper ..."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bot.research.ai_analyst",
        description="S46 AI Research Analyst + S47 Paper Trading",
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
    return p


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
