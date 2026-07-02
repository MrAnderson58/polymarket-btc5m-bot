"""Markdown and JSON rendering for analytics report v3."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR
from bot.strategy_review.render import render_strategy_review_section


def _json_default(value: Any) -> Any:
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        if math.isnan(value):
            return None
    return value


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=_json_default, ensure_ascii=False)


def _h2(title: str) -> list[str]:
    return ["", f"## {title}", ""]


def _metrics_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    header = "| " + " | ".join(c[0] for c in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for row in rows:
        cells = []
        for _, key in columns:
            val = row.get(key, "")
            if isinstance(val, float):
                if key in ("win_rate",):
                    cells.append(f"{val:.1%}")
                elif "profit_factor" in key or key == "profit_factor":
                    cells.append(f"{val:.2f}" if val != float("inf") else "inf")
                elif "stability" in key or key.endswith("_pct"):
                    cells.append(f"{val:.0f}%")
                else:
                    cells.append(f"{val:+.2f}")
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _render_heatmap_block(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [f"**{title}**", ""]
    if not rows:
        lines.append("_Insufficient data_")
        return lines
    for row in rows:
        lines.append(f"- {row.get('line', row.get('label', ''))}")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Trading Analytics Report v4",
        "",
        f"Generated: {report['meta']['generated_at']}",
        "",
        "> Trading Intelligence Engine — read-only. Trading logic is not modified.",
        "",
    ]

    cache_note = report.get("optimizer_cache_note") or report.get("meta", {}).get(
        "optimizer_cache_note"
    )
    if cache_note:
        lines += ["---", "", cache_note, "", "---", ""]

    intel_note = report.get("intelligence_cache_note")
    if intel_note and intel_note != cache_note:
        lines += ["---", "", intel_note, "", "---", ""]

    cfg = report["configuration"]
    lines += _h2("1. BOT CONFIGURATION")
    for key, val in cfg.items():
        lines.append(f"- **{key}**: {val}")

    lines += _h2("2. BOT HEALTH")
    for key, val in report["bot_health"].items():
        lines.append(f"- **{key}**: {val}")
    rh = report["recovery_health"]
    lines.append("\n**Recovery Health**")
    for key, val in rh.items():
        lines.append(f"- {key}: {val}")

    lines += _h2("3. CURRENT POSITION STATUS")
    pos = report["current_positions"]
    if not pos["positions"]:
        lines.append("No open positions.")
    else:
        lines += _metrics_table(
            pos["positions"],
            [
                ("market", "market"),
                ("strategy", "strategy"),
                ("entry", "entry"),
                ("bid", "current_bid"),
                ("pnl%", "current_pnl_pct"),
                ("hold s", "holding_sec"),
                ("trail", "trailing_active"),
            ],
        )
    for warning in pos.get("warnings", []):
        lines.append(f"\n> **{warning['level']}** — Trade {warning['trade_id']}: {warning['message']}")

    lines += _h2("4. ENTRY FUNNEL")
    for section in report["entry_funnel"]["sections"]:
        lines.append(
            f"**{section['window']} / {section['strategy']}** — "
            f"Checks: {section['checks']}, Ready: {section.get('ready', 0)}, "
            f"Entries: {section['entries']}, Blocked: {section.get('blocked', 0)}"
        )

    lines += _h2("5. ENTRY PRICE ANALYSIS")
    ep = report["entry_price_analysis"]
    lines += _metrics_table(
        ep["rows"],
        [
            ("Entry", "entry_price"),
            ("Trades", "trades"),
            ("WR", "win_rate"),
            ("Avg PnL", "avg_pnl"),
            ("PF", "profit_factor"),
            ("Net", "net_profit"),
        ],
    )
    lines.append(
        f"\n**Best entry price:** {ep.get('best_entry_price')} | "
        f"**Worst:** {ep.get('worst_entry_price')}"
    )

    lines += _h2("6. EXIT ANALYSIS")
    for reason, m in report["exit_analysis"].items():
        if m["trades"] == 0:
            continue
        lines.append(
            f"- **{reason}**: {m['trades']} trades, WR {m['win_rate']:.0%}, "
            f"Avg {m['avg_pnl']:+.2f}%, PF {m['profit_factor']:.2f}, Net {m['net_profit']:+.1f}%"
        )

    lines += _h2("7. STOP LOSS ANALYSIS")
    for key, val in report["stop_loss_analysis"].items():
        lines.append(f"- **{key}**: {val}")

    lines += _h2("8. STOP LOSS SHORT RECOVERY")
    sr = report["stop_loss_short_recovery"]
    lines.append(f"Total STOP_LOSS: {sr['total_stop_loss']}")
    for sec in (15, 30, 45, 60):
        lines.append(
            f"- Entry recovered {sec}s: {sr[f'recovered_entry_{sec}s_count']} "
            f"({sr[f'recovered_entry_{sec}s_rate']:.0%})"
        )
    lines.append(
        f"- Never recovered 60s: {sr['never_recovered_60s_count']} "
        f"({sr['never_recovered_60s_rate']:.0%})"
    )

    lines += _h2("9. ALTERNATIVE STOP TEST")
    for label, m in report["alternative_stop_test"].items():
        lines.append(
            f"- **{label}%**: WR {m['win_rate']:.0%}, Avg {m['avg_pnl']:+.2f}%, "
            f"PF {m['profit_factor']:.2f}, Net {m['net_profit']:+.1f}%"
        )

    lines += _h2("10. TRAILING ANALYSIS")
    for strategy, ta in report["trailing_analysis"].items():
        lines.append(
            f"**{strategy}** — activation {ta['activation_rate']:.0%}, "
            f"avg peak {ta['avg_peak_before_exit']:.3f}"
        )
    best = report["trailing_simulation"].get("best")
    if best:
        lines.append(
            f"\nBest offline sim: activation={best['activation']}, "
            f"distance={best['distance']}, Net {best['net_profit']:+.1f}%"
        )

    lines += _h2("11. BTC DIRECTION")
    for strategy, buckets in report["btc_direction"].items():
        lines.append(f"**{strategy}**")
        for b in buckets:
            lines.append(
                f"- {b['direction']}: {b['trades']} trades, WR {b['win_rate']:.0%}, "
                f"Avg {b['avg_pnl']:+.1f}%"
            )

    lines += _h2("12. BTC FILTER ANALYSIS")
    bf = report["btc_filter_analysis"]
    lines += _metrics_table(
        bf["rows"],
        [
            ("Bucket", "bucket"),
            ("Trades", "trades"),
            ("WR", "win_rate"),
            ("PF", "profit_factor"),
            ("Avg", "avg_pnl"),
            ("Net", "net_profit"),
        ],
    )
    lines.append(f"\nCurrent: {bf['current_thresholds']}")
    lines.append(f"Recommended: {bf['recommended_thresholds']}")

    lines += _h2("13. SHADOW STRATEGIES")
    for name, data in report.get("shadow_strategies", {}).items():
        if isinstance(data, dict) and "trades" in data:
            lines.append(
                f"- **{name}**: {data['trades']} trades, WR {data['win_rate']:.0%}, "
                f"Avg {data['avg_pnl']:+.2f}%"
            )

    lines += _h2("14. EXECUTION QUALITY")
    for key, val in report["execution_quality"].items():
        lines.append(f"- **{key}**: {val}")

    lines += _h2("15. HOLDING TIME")
    lines += _metrics_table(
        report["holding_time_analysis"]["rows"],
        [
            ("Max sec", "max_holding_sec"),
            ("Trades", "trades"),
            ("WR", "win_rate"),
            ("Avg", "avg_pnl"),
            ("PF", "profit_factor"),
            ("Net", "net_profit"),
        ],
    )

    lines += _h2("16. MAE / MFE")
    mae_mfe = report["mae_mfe"]
    lines.append(
        f"- MAE: avg {mae_mfe['mae']['avg']:+.2f}%, median {mae_mfe['mae']['median']:+.2f}%"
    )
    lines.append(
        f"- MFE: avg {mae_mfe['mfe']['avg']:+.2f}%, median {mae_mfe['mfe']['median']:+.2f}%"
    )

    lines += _h2("17. MARKET REGIME")
    for regime, m in report["market_regime"].items():
        if m["trades"]:
            lines.append(
                f"- **{regime}**: {m['trades']} trades, WR {m['win_rate']:.0%}, "
                f"Avg {m['avg_pnl']:+.2f}%, PF {m['profit_factor']:.2f}"
            )

    lines += _h2("18. WALK FORWARD")
    wf = report["walk_forward"]
    for row in wf.get("rows", []):
        label = row.get("label") or (
            f"Train {row.get('train_size', '?')} → Test {row.get('test_size', '?')}"
        )
        if row.get("win_rate") is not None:
            lines.append(
                f"- **{label}**: WR {row['win_rate']:.0%}, Avg {row.get('avg_pnl', 0):+.2f}%, "
                f"PF {row.get('profit_factor', 0):.2f}, Net {row.get('net_profit', 0):+.1f}%"
            )
        else:
            lines.append(
                f"- **{label}**: Test Avg {row.get('test_avg_pnl', row.get('avg_pnl', 0)):+.2f}%, "
                f"Test PF {row.get('test_pf', row.get('profit_factor', 0)):.2f}, "
                f"Generalizes: {'yes' if row.get('generalizes') else 'no'}"
            )
    lines.append(f"\n**Trend:** {wf.get('trend', 'insufficient_data')}")

    lines += _h2("19. AI RECOMMENDATIONS")
    for rec in report["recommendations"]:
        lines.append(f"- {rec}")

    lines += _h2("20. FINAL SCORE")
    for key, val in report["final_score"].items():
        lines.append(f"- **{key}**: {val}/100")

    lines += _h2("21. PARAMETER STABILITY SCORE")
    for p in report.get("parameter_stability", {}).get("parameters", []):
        val = p.get("value")
        val_s = f"{val:.2f}" if isinstance(val, float) and val < 1 else str(val)
        lines.append(
            f"- **{p['parameter']} {val_s}** — Trades {p['trades']}, "
            f"PF {p['profit_factor']:.2f}, WR {p['win_rate']:.0%}, "
            f"Stability {p['stability_pct']:.0f}% ({p['stability_label']}), "
            f"Confidence {p['confidence']}"
        )

    lines += _h2("22. OVERFIT DETECTOR")
    of = report.get("overfit_detector", {})
    lines.append(f"**Level:** {of.get('level', 'LOW')}")
    lines.append(f"**Recommendation:** {of.get('recommendation', '')}")
    lines.append("\n**Reasons:**")
    for reason in of.get("reasons", []):
        lines.append(f"- {reason}")

    lines += _h2("23. HEATMAP")
    hm = report.get("heatmaps", {})
    for key, title in (
        ("entry_threshold", "ENTRY THRESHOLD"),
        ("stop_loss", "STOP LOSS"),
        ("trailing", "TRAILING"),
        ("holding_time", "HOLDING TIME"),
        ("btc_filter", "BTC FILTER"),
    ):
        lines += [""] + _render_heatmap_block(title, hm.get(key, []))

    lines += _h2("24. SENSITIVITY ANALYSIS")
    for p in report.get("sensitivity_analysis", {}).get("parameters", []):
        lines.append(f"**{p['parameter']}** (current {p['current']}) — Sensitivity **{p['sensitivity']}**")
        for step in p.get("steps", []):
            direction = "↓" if step["to"] < step["from"] else "↑"
            lines.append(
                f"- {direction} {step['to']}: PF {step.get('pf_change_pct', 0):+.0f}%"
            )

    lines += _h2("25. SAFE TO CHANGE")
    for item in report.get("safe_to_change", []):
        lines.append(f"**{item['parameter']}** — **{item['status']}**")
        lines.append(f"- Confidence {item['confidence_pct']:.0f}%")
        lines.append(f"- {item['reason']}")
        lines.append("")

    lines += _h2("26. AI DECISION ENGINE")
    dec = report.get("ai_decision", {})
    lines.append(f"**Overall Score:** {dec.get('overall_score', 0)}/100")
    lines.append(f"**Market Regime:** {dec.get('market_regime', 'N/A')}")
    lines.append(f"**Recommendation:** {dec.get('recommendation', 'KEEP CURRENT SETTINGS')}")
    if dec.get("insufficient_live_data"):
        lines.append("\n**INSUFFICIENT LIVE DATA**")
        lines.append(f"- Current sample: {dec.get('current_sample', 0)} trades")
        lines.append(
            f"- Required: {dec.get('required_min', 300)}–{dec.get('required_target', 500)} trades"
        )
    if dec.get("change_from") is not None:
        lines.append(
            f"- Change: {dec.get('change_from')} → {dec.get('change_to')} "
            f"(expected +{dec.get('expected_improvement_pct', 0):.1f}%)"
        )
    lines.append(f"**Confidence:** {dec.get('confidence_pct', 0):.0f}%")
    for reason in dec.get("reasons", []):
        lines.append(f"- {reason}")

    lines += _h2("27. AI MEMORY")
    mem = report.get("ai_memory", {})
    lines.append(f"Total indexed reports: {mem.get('entries', 0)}")
    for window, label in (("7", "7 reports"), ("14", "14 reports"), ("30", "30 reports")):
        summary = mem.get("trends", {}).get(window, {})
        if summary:
            lines.append(
                f"- **Last {label}:** PF Δ{summary.get('pf_delta', 0):+.2f}, "
                f"Score Δ{summary.get('score_delta', 0):+.0f}, "
                f"trend {summary.get('direction', 'flat')}"
            )

    lines += _h2("28. EXPERIMENT MANAGER")
    for exp in report.get("experiments", [])[-5:]:
        changes = exp.get("changes", [])
        change_txt = ""
        if changes:
            c = changes[0]
            change_txt = f" | {c.get('parameter')} {c.get('from')} → {c.get('to')}"
        milestones = exp.get("milestones", {})
        ms = ", ".join(f"{k}: {v or 'Pending'}" for k, v in milestones.items())
        lines.append(
            f"- #{exp.get('id')} {exp.get('started_at', '')[:10]}{change_txt} | "
            f"PF {exp.get('profit_factor', 0):.2f} | {exp.get('result', 'Pending')} | {ms}"
        )

    lines += _h2("29. LIVE VS OPTIMAL")
    lvo = report.get("live_vs_optimal", {})
    live = lvo.get("live", {})
    replay = lvo.get("replay", {})
    lines.append(f"- **LIVE** PF {live.get('profit_factor', 0):.2f}")
    lines.append(f"- **Replay** PF {replay.get('profit_factor', 0):.2f}")
    lines.append(f"- **Gap** {lvo.get('gap_pct', 0):.0f}%")
    lines.append(f"- **Reason:** {lvo.get('reason', '')}")

    lines += _h2("30. FINAL ACTION PLAN")
    plan = report.get("final_action_plan", {})
    stars = "★" * plan.get("priority", 3)
    lines.append(f"**TODAY:** {plan.get('today', 'MONITOR')}")
    if plan.get("need_trades"):
        lines.append(f"- Need {plan['need_trades']} more trades")
    if plan.get("expected_review"):
        lines.append(f"- Expected review: {plan['expected_review']}")
    if plan.get("change_to") is not None:
        lines.append(
            f"- Change {plan.get('change_from')} → {plan.get('change_to')} ONLY"
        )
    dont = plan.get("do_not_touch", [])
    if dont:
        lines.append(f"- Do not touch: {', '.join(dont)}")
    lines.append(f"- Priority: {stars}")
    if plan.get("reason"):
        lines.append(f"- {plan['reason']}")

    lines += _h2("31. EXECUTION AUDIT")
    ea = report.get("execution_audit", {})
    sm = ea.get("summary", {})
    lines.append(f"- **Execution Loss:** {sm.get('execution_loss_pct', 0):.1f}% cumulative")
    lines.append(f"- **Strategy Loss:** {sm.get('strategy_loss_pct', 0):.1f}%")
    lines.append(f"- **Exchange Loss:** {sm.get('exchange_loss_pct', 0):.1f}%")
    for t in ea.get("trades", [])[-5:]:
        liq = "Yes" if t.get("liquidity_ok") else "No"
        lines.append(
            f"- #{t['trade_id']} {t['actual_pnl_pct']:+.1f}% | "
            f"slip {t.get('slippage_pct', 'n/a')} | liquidity {liq}"
        )

    lines += _h2("32. STOP QUALITY")
    sq = report.get("stop_quality", {})
    rc = sq.get("rating_counts", {})
    lines.append(
        f"GOOD {rc.get('GOOD', 0)} | BAD {rc.get('BAD', 0)} | "
        f"CRITICAL {rc.get('CRITICAL', 0)}"
    )
    for s in sq.get("stops", [])[-5:]:
        lines.append(
            f"- STOP #{s['trade_id']}: {s['actual_pnl_pct']:+.0f}% "
            f"(excess {s['excess_loss_pct']:.0f}%) — **{s['rating']}** ({s['cause']})"
        )

    lines += _h2("33. RECOVERY ANALYZER")
    ra = report.get("recovery_analyzer", {})
    for h in ra.get("alternative_holds", []):
        lines.append(
            f"- Hold {h['hold_sec']}s: WR {h['win_rate']:.0%}, PF {h['profit_factor']:.2f}"
        )

    lines += _h2("34. FALSE STOP DETECTOR")
    fs = report.get("false_stop_detector", {})
    lines.append(
        f"False {fs.get('false_stops', 0)} | True {fs.get('true_stops', 0)} | "
        f"Late {fs.get('late_stops', 0)} ({fs.get('false_stop_rate', 0):.0%} false rate)"
    )

    lines += _h2("35. MARKET REGIME ENGINE")
    for name, m in report.get("regime_engine", {}).get("regimes", {}).items():
        if m.get("trades", 0) >= 3:
            lines.append(f"- **{name}**: PF {m['profit_factor']:.2f}, WR {m['win_rate']:.0%}")

    lines += _h2("36. SIGNAL QUALITY")
    sig = report.get("signal_quality", {})
    lines.append(
        f"High-score WR {sig.get('high_score_win_rate', 0):.0%} vs "
        f"low-score {sig.get('low_score_win_rate', 0):.0%}"
    )

    lines += _h2("37. FEATURE DRIFT")
    for alert in report.get("feature_drift", {}).get("alerts", []):
        lines.append(f"- {alert.get('message', alert)}")

    lines += _h2("38. REGIME CHANGE DETECTOR")
    rcd = report.get("regime_change_detector", {})
    lines.append(f"**{rcd.get('level', 'LOW')}** — {rcd.get('message', '')}")

    lines += _h2("39. CAPITAL SIMULATOR")
    cap = report.get("capital_simulator", {})
    lines.append(f"Path ($100): {cap.get('primary_path_100usd', '')}")
    for sc in cap.get("scenarios", []):
        lines.append(
            f"- ${sc['starting_usd']:,.0f} → ${sc['ending_usd']:,.0f} "
            f"(DD ${sc['max_drawdown_usd']:,.0f})"
        )

    lines += _h2("40. AI RESEARCH NOTEBOOK")
    nb = report.get("research_notebook", {})
    lines.append(f"_{nb.get('pipeline_reminder', '')}_")
    for i, f in enumerate(nb.get("findings", []), 1):
        lines.append(f"{i}. {f['finding']} (confidence {f['confidence_pct']:.0f}%)")
        if f.get("hypothesis"):
            lines.append(f"   → Hypothesis: {f['hypothesis']}")

    lines += _h2("41. AI AGENT (observe-only)")
    agent = report.get("ai_agent", {})
    lines.append(f"_{agent.get('disclaimer', '')}_")
    lines.append(f"Mode: **{agent.get('meta', {}).get('mode', 'observe_only')}** | "
                 f"Signals: {agent.get('meta', {}).get('signals_recorded', 0)}")
    lines.append("\n**AI Score distribution**")
    for b in agent.get("score_distribution", []):
        bar = "█" * min(20, b["count"])
        lines.append(f"- {b['bucket']}: {bar} ({b['count']})")
    dec = agent.get("decisions", {})
    lines.append(
        f"\n**Decisions (shadow):** ALLOW {dec.get('ALLOW', 0)} | "
        f"SKIP {dec.get('SKIP', 0)} | SHADOW {dec.get('SHADOW', 0)}"
    )
    dq = agent.get("decision_quality", {})
    for key in ("allow", "skip", "shadow"):
        s = dq.get(key, {})
        if s.get("trades"):
            lines.append(
                f"- {key.upper()}: {s['trades']} trades, WR {s['win_rate']:.0%}, "
                f"Avg {s['avg_pnl']:+.2f}%"
            )
    if dq.get("counterfactual_edge_pct"):
        lines.append(
            f"- Counterfactual SKIP edge vs ALLOW: {dq['counterfactual_edge_pct']:+.2f}% avg"
        )
    lines.append("\n**Patterns**")
    for p in agent.get("patterns", [])[:5]:
        lines.append(
            f"- {p['pattern']} (n={p['trades']}, conf {p['confidence_pct']:.0f}%)"
        )
    lines.append("\n**Shadow experiment recommendations**")
    for rec in agent.get("shadow_recommendations", []):
        lines.append(f"- {rec}")
    lines.append("\n**ML models (prepared, inactive in v1)**")
    for m in agent.get("models", []):
        status = "active" if m.get("active_in_v1") else "standby"
        avail = "yes" if m.get("available") else "no"
        lines.append(f"- {m['name']}: {status} (installed: {avail})")

    intel = agent.get("intelligence", {})
    lines += _h2("42. AI INTELLIGENCE")
    lines.append(
        f"- Average AI Score: **{intel.get('average_ai_score', 0)}** | "
        f"Average Confidence: **{intel.get('average_confidence', 0)}%**"
    )
    lines.append("\n**Top Reasons**")
    for r in intel.get("top_reasons", [])[:5]:
        lines.append(f"- {r['reason']} ({r['count']}x)")
    tsp = intel.get("top_similar_pattern")
    if tsp:
        lines.append(
            f"\n**Top Similar Pattern:** trade #{tsp.get('trade_id')} "
            f"(n={tsp.get('similar_count')}, PF {tsp.get('historical_pf')})"
        )
    cm = intel.get("counterfactual_matrix", {})
    matrix = cm.get("matrix", {})
    lines.append(
        f"\n**Counterfactual Matrix:** TP {matrix.get('TP', 0)} | "
        f"FP {matrix.get('FP', 0)} | TN {matrix.get('TN', 0)} | FN {matrix.get('FN', 0)}"
    )
    lines.append(
        f"- False Allow: **{intel.get('false_allow_pct', 0):.0%}** | "
        f"False Skip: **{intel.get('false_skip_pct', 0):.0%}**"
    )
    if intel.get("best_regime"):
        lines.append(
            f"- Best regime: **{intel['best_regime']}** | "
            f"Worst: **{intel.get('worst_regime', 'n/a')}**"
        )
    trend = intel.get("learning_trend", {})
    if trend:
        lines.append(
            f"\n**AI Learning Trend:** avg score {trend.get('avg_score', 0)} | "
            f"avg confidence {trend.get('avg_confidence', 0)}"
        )
    lines.append(f"- Today's new trades: {intel.get('today_new_trades', 0)}")

    brain = report.get("trading_brain", {})
    lines += _h2("43. TRADING BRAIN (observe-only)")
    lines.append(f"_{brain.get('disclaimer', '')}_")
    lines.append(
        f"Version **{brain.get('version', '?')}** | Mode: **{brain.get('mode', 'observe_only')}**"
    )
    lr = brain.get("last_run", {})
    if lr:
        lines.append(
            f"- Trades processed: {lr.get('trades_processed', 0)} | "
            f"Contexts: {lr.get('contexts_built', 0)} | "
            f"Knowledge links: {lr.get('knowledge_links', 0)}"
        )
        lines.append(f"- New since last run: {lr.get('new_trades_since_last', 0)}")
    mem = brain.get("memory_sync_counts") or brain.get("memory_entries") or {}
    if mem:
        parts = [f"{k}={v}" for k, v in sorted(mem.items())]
        lines.append(f"- Memory: {', '.join(parts)}")
    lines.append(f"- Trade contexts stored: {brain.get('contexts_stored', 0)}")
    lines.append("\n**Top causal knowledge**")
    for link in brain.get("top_knowledge", [])[:5]:
        lines.append(
            f"- {link['condition']}: {link['direction']} "
            f"{link['effect']:+.1f}% (conf {link['confidence']:.0f}%, n={link['sample_n']})"
        )
    sample = brain.get("latest_explainability_sample", {})
    if sample.get("trade_id"):
        lines.append(f"\n**Latest explainability sample** (trade #{sample['trade_id']})")
        for r in sample.get("supportive", []):
            lines.append(f"- + {r}")
        for r in sample.get("opposing", []):
            lines.append(f"- − {r}")

    sci = report.get("scientist", {})
    lines += _h2("44. AI SCIENTIST (observe-only)")
    lines.append(f"_{sci.get('disclaimer', '')}_")
    lines.append(
        f"Version **{sci.get('version', '?')}** | Mode: **{sci.get('mode', 'observe_only')}**"
    )
    summ = sci.get("summary", {})
    if summ:
        lines.append(
            f"- Patterns: {summ.get('patterns_found', 0)} | "
            f"New hypotheses: {summ.get('new_hypotheses', 0)} | "
            f"Passed: {summ.get('passed', 0)} | "
            f"Failed/rejected: {summ.get('failed', 0)}"
        )
    lines.append("\n**New hypotheses**")
    for h in sci.get("new_hypotheses", [])[:5]:
        lines.append(f"- {h.get('description', '')[:100]}")
    lines.append("\n**Top experiments**")
    for exp in sci.get("top_experiments", [])[:5]:
        lines.append(
            f"- [{exp.get('status')}] {exp.get('title')} — "
            f"conf {float(exp.get('confidence', 0)):.0f}%, "
            f"priority {exp.get('priority')}, risk {exp.get('risk_level')}"
        )
    best = sci.get("best_next_step", {})
    lines.append("\n**Best next step**")
    if best.get("blocked"):
        lines.append(f"- BLOCKED: {best.get('reason', '')}")
    else:
        lines.append(f"- **{best.get('recommendation')}** (+{best.get('expected_pf_pct', 0):.0f}% PF)")
        lines.append(
            f"  Confidence {best.get('confidence_pct', 0):.0f}% | "
            f"Risk {best.get('risk')} | Priority {best.get('priority')}"
        )
        if best.get("reasons"):
            lines.append(f"  - {' | '.join(best['reasons'])}")

    sr = report.get("strategy_review", {})
    lines += _h2("45. STRATEGY REVIEW (observe-only)")
    lines.extend(render_strategy_review_section(sr))

    lines += _h2("APPENDIX: Parameter Optimizer")
    opt = report.get("parameter_optimizer", {})
    cur, best = opt.get("current", {}), opt.get("optimal", {})
    lines.append(
        f"Current entry {cur.get('entry', '?')} | Optimal {best.get('entry', '?')} | "
        f"Expected +{opt.get('expected_improvement_pct', 0):.1f}%"
    )

    lines += _h2("APPENDIX: Feature Importance & Monte Carlo")
    for item in report.get("feature_importance", []):
        lines.append(f"- {item['feature']}: {item['importance_pct']}%")
    mc = report.get("monte_carlo", {})
    if mc:
        lines.append(
            f"- Monte Carlo ruin: {mc.get('probability_of_ruin', 0):.1%}, "
            f"max DD {mc.get('expected_max_drawdown_pct', 0):.1f}%"
        )

    op = report["overall_performance"]
    lines += _h2("APPENDIX: Overall Performance")
    lines.append(
        f"Closed trades: {op['trades']}, WR {op['win_rate']:.0%}, "
        f"Avg {op['avg_pnl']:+.2f}%, PF {op['profit_factor']:.2f}, Net {op['net_profit']:+.1f}%"
    )

    lines.append(
        "\n---\n\n**Do not apply recommendations automatically.** "
        "Review SAFE TO CHANGE and FINAL ACTION PLAN before any live changes."
    )
    return "\n".join(lines).rstrip() + "\n"


def write_report_files(
    report: dict[str, Any],
    *,
    reports_dir: Path | None = None,
    stamp: datetime | None = None,
) -> tuple[Path, Path]:
    if reports_dir is None:
        reports_dir = BASE_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    if stamp is None:
        stamp = datetime.now(timezone.utc)
    stamp_str = stamp.strftime("%Y-%m-%d_%H-%M")
    md_path = reports_dir / f"report_{stamp_str}.md"
    json_path = reports_dir / f"report_{stamp_str}.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")
    return md_path, json_path
