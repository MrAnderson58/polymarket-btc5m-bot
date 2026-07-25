"""S64 — PnL Killer Analyzer (research-only).

Find which closed-trade segments generate and destroy PnL.
Statistics only. No strategy changes. No AI. No production writes.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from bot.research.market_events.signal_intelligence.drift_analyzer_s622 import (
    _enrich_from_snapshot_json,
)
from bot.research.market_events.signal_intelligence.feature_lab_s59 import load_lab_trades
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    normalize_coin,
    normalize_score_0_100,
)
from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
    AI_BUCKETS,
    FUNDING_BUCKETS,
    WEEKDAYS,
    _enrich_decisions,
    _regime_family,
    _safe_float,
    _trade_ts,
    overall_performance,
)

logger = logging.getLogger(__name__)

TOP_N = 20

CONFIDENCE_BUCKETS = (
    ("Conf 0-20", lambda x: 0 <= x < 20),
    ("Conf 20-40", lambda x: 20 <= x < 40),
    ("Conf 40-60", lambda x: 40 <= x < 60),
    ("Conf 60-80", lambda x: 60 <= x < 80),
    ("Conf 80-100", lambda x: 80 <= x <= 100),
)

DIM_LABEL = {
    "strategy": "Strategy",
    "coin": "Coin",
    "direction": "Direction",
    "regime": "Regime",
    "hour": "Hour UTC",
    "weekday": "Weekday",
    "confidence": "Confidence",
    "funding": "Funding",
    "ai_score": "AI score",
    "entry_reason": "Entry reason",
}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def pnl_report_dir(root: Path | None = None) -> Path:
    return (root or _repo_root()) / "research" / "reports" / "pnl"


def _fmt(v: Any, *, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 100:
        return f"{f:.2f}"
    return f"{f:.{digits}g}"


def _bucket_name(value: float | None, specs: tuple) -> str:
    if value is None:
        return "unknown"
    for name, pred in specs:
        try:
            if pred(value):
                return name
        except Exception:
            continue
    return "unknown"


def _ai_scaled(r: dict[str, Any]) -> float | None:
    return normalize_score_0_100(_safe_float(r.get("ai_score")))


def _confidence_scaled(r: dict[str, Any]) -> float | None:
    for key in ("decision_confidence", "confidence", "entry_confidence", "dynamic_confidence"):
        v = normalize_score_0_100(_safe_float(r.get(key)))
        if v is None:
            continue
        return v
    raw = r.get("snapshot_json")
    if raw:
        try:
            d = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            d = None
        if isinstance(d, dict):
            for key in ("decision_confidence", "confidence", "entry_confidence"):
                v = normalize_score_0_100(_safe_float(d.get(key)))
                if v is None:
                    continue
                return v
    return None


def _entry_reason_key(r: dict[str, Any]) -> str:
    """Entry-side reasons only — never exit_reason / stop outcomes."""
    why = r.get("why_opened")
    if isinstance(why, str):
        try:
            why = json.loads(why)
        except Exception:
            why = None
    if isinstance(why, list) and why:
        tags: list[str] = []
        for item in why[:3]:
            if isinstance(item, dict):
                tag = item.get("tag") or item.get("reason")
                if tag:
                    tags.append(str(tag))
            elif item:
                tags.append(str(item)[:40])
        if tags:
            return " + ".join(tags)[:120]
    gate = r.get("gate_reason")
    if gate is not None:
        g = str(gate).strip()
        if g and g not in ("—", "PASS", "FAIL", "None", "null"):
            return g[:120]
    return "unknown"


def _segment_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    m = overall_performance(rows)
    n = int(m.get("trades") or 0)
    pnl = float(m.get("pnl") or 0.0)
    return {
        "trades": n,
        "win_rate": m.get("winrate"),
        "profit_factor": m.get("profit_factor"),
        "pf_inf": bool(m.get("pf_inf")),
        "net_pnl": pnl,
        "avg_pnl": round(pnl / n, 4) if n else None,
        "max_dd": m.get("max_drawdown"),
    }


def _card(dimension: str, key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _segment_metrics(rows)
    return {
        "dimension": dimension,
        "key": key,
        "label": f"{DIM_LABEL.get(dimension, dimension)}={key}",
        **metrics,
    }


def _group_cards(
    rows: list[dict[str, Any]],
    *,
    dimension: str,
    key_fn: Callable[[dict[str, Any]], str | None],
) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = key_fn(r)
        if key is None or key == "":
            key = "unknown"
        by[str(key)].append(r)
    cards = [_card(dimension, k, members) for k, members in by.items() if members]
    cards.sort(key=lambda c: (-float(c.get("net_pnl") or 0.0), -(c.get("trades") or 0)))
    return cards


def _coin(r: dict[str, Any]) -> str:
    return normalize_coin(r.get("symbol") or "?") or "unknown"


def _strategy(r: dict[str, Any]) -> str:
    return str(
        r.get("s40_signal_type")
        or r.get("strategy_label")
        or r.get("strategy")
        or "unknown"
    ).strip() or "unknown"


def _hour(r: dict[str, Any]) -> str | None:
    try:
        h = int(r.get("hour"))
    except (TypeError, ValueError):
        ts = _trade_ts(r)
        if ts <= 0:
            return None
        from datetime import datetime, timezone
        h = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    if 0 <= h <= 23:
        return f"H{h:02d}"
    return None


def _weekday(r: dict[str, Any]) -> str | None:
    try:
        w = int(r.get("weekday"))
        if 0 <= w <= 6:
            return WEEKDAYS[w]
    except (TypeError, ValueError, IndexError):
        pass
    ts = _trade_ts(r)
    if ts <= 0:
        return None
    from datetime import datetime, timezone
    return WEEKDAYS[datetime.fromtimestamp(ts, tz=timezone.utc).weekday()]


def _direction(r: dict[str, Any]) -> str:
    return str(r.get("direction") or "unknown").upper()


def _regime(r: dict[str, Any]) -> str:
    return _regime_family(r.get("market_regime"))


DIM_KEY_FNS: dict[str, Callable[[dict[str, Any]], str | None]] = {
    "strategy": _strategy,
    "coin": _coin,
    "direction": _direction,
    "regime": _regime,
    "hour": _hour,
    "weekday": _weekday,
    "confidence": lambda r: _bucket_name(_confidence_scaled(r), CONFIDENCE_BUCKETS),
    "funding": lambda r: _bucket_name(_safe_float(r.get("funding")), FUNDING_BUCKETS),
    "ai_score": lambda r: _bucket_name(_ai_scaled(r), AI_BUCKETS),
    "entry_reason": _entry_reason_key,
}


def dimension_value(r: dict[str, Any], dimension: str) -> str:
    """Resolve a trade's categorical value for a S64 dimension."""
    fn = DIM_KEY_FNS.get(dimension)
    if fn is None:
        return "unknown"
    key = fn(r)
    if key is None or key == "":
        return "unknown"
    return str(key)


def build_dimension_tables(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        dim: _group_cards(rows, dimension=dim, key_fn=fn)
        for dim, fn in DIM_KEY_FNS.items()
    }


def rank_generators(
    by_dimension: dict[str, list[dict[str, Any]]],
    *,
    top_n: int = TOP_N,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_cards: list[dict[str, Any]] = []
    for cards in by_dimension.values():
        all_cards.extend(cards)
    profit = sorted(
        [c for c in all_cards if float(c.get("net_pnl") or 0.0) > 0],
        key=lambda c: (-float(c.get("net_pnl") or 0.0), -(c.get("trades") or 0)),
    )[:top_n]
    loss = sorted(
        [c for c in all_cards if float(c.get("net_pnl") or 0.0) < 0],
        key=lambda c: (float(c.get("net_pnl") or 0.0), -(c.get("trades") or 0)),
    )[:top_n]
    return profit, loss


def format_pnl_killers_markdown(report: dict[str, Any]) -> str:
    overall = report.get("overall") or {}
    lines = [
        "# PnL Killer Analyzer (S64)",
        "",
        f"_trades={report.get('n_trades')} net_pnl={_fmt(overall.get('net_pnl'))} "
        f"elapsed={report.get('elapsed_sec')}s_",
        "",
        "Statistics only. No strategy changes. No AI.",
        "",
        "## Overall",
        "",
        f"- Trades: **{overall.get('trades')}**",
        f"- Win Rate: **{_fmt(overall.get('win_rate'))}%**",
        f"- Profit Factor: **{'∞' if overall.get('pf_inf') else _fmt(overall.get('profit_factor'))}**",
        f"- Net PnL: **{_fmt(overall.get('net_pnl'))}**",
        f"- Avg PnL: **{_fmt(overall.get('avg_pnl'))}**",
        f"- Max DD: **{_fmt(overall.get('max_dd'))}**",
        "",
    ]

    def _table(title: str, cards: list[dict[str, Any]]) -> None:
        lines.extend([
            f"## {title}",
            "",
            "| # | Segment | Trades | Win Rate | PF | Net PnL | Avg PnL | Max DD |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ])
        if not cards:
            lines.append("| — | _none_ | | | | | | |")
        for i, c in enumerate(cards, 1):
            pf = "∞" if c.get("pf_inf") else _fmt(c.get("profit_factor"))
            lines.append(
                f"| {i} | {c.get('label')} | {c.get('trades')} | {_fmt(c.get('win_rate'))} | "
                f"{pf} | {_fmt(c.get('net_pnl'))} | {_fmt(c.get('avg_pnl'))} | "
                f"{_fmt(c.get('max_dd'))} |"
            )
        lines.append("")

    _table("Top 20 Profit Generators", report.get("top_profit_generators") or [])
    _table("Top 20 Loss Generators (PnL Killers)", report.get("top_loss_generators") or [])

    by_dim = report.get("by_dimension") or {}
    for dim, cards in by_dim.items():
        _table(f"By {DIM_LABEL.get(dim, dim)}", cards)

    return "\n".join(lines).rstrip() + "\n"


def format_pnl_killers_summary(report: dict[str, Any]) -> str:
    overall = report.get("overall") or {}
    lines = [
        "S64 PnL Killer Analyzer",
        f"  trades={report.get('n_trades')} net_pnl={overall.get('net_pnl')} "
        f"elapsed={report.get('elapsed_sec')}s",
    ]
    best = (report.get("top_profit_generators") or [None])[0]
    worst = (report.get("top_loss_generators") or [None])[0]
    lines.append(
        f"  top_profit={best.get('label') if best else '—'} "
        f"pnl={best.get('net_pnl') if best else '—'}"
    )
    lines.append(
        f"  top_killer={worst.get('label') if worst else '—'} "
        f"pnl={worst.get('net_pnl') if worst else '—'}"
    )
    for k, p in (report.get("export_paths") or {}).items():
        lines.append(f"  {k}: {p}")
    return "\n".join(lines)


def run_pnl_killers(
    conn: Any,
    *,
    report_dir: Path | None = None,
    top_n: int = TOP_N,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rows = load_lab_trades(conn)
    _enrich_decisions(conn, rows)
    _enrich_from_snapshot_json(rows)

    closed = [r for r in rows if r.get("pnl_usd") is not None]
    overall = _segment_metrics(closed)
    by_dimension = build_dimension_tables(closed)
    profit, loss = rank_generators(by_dimension, top_n=top_n)

    out_dir = Path(report_dir) if report_dir else pnl_report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "pnl_killers.md"
    json_path = out_dir / "pnl_killers.json"

    report: dict[str, Any] = {
        "ok": True,
        "stage": "S64",
        "n_trades": len(closed),
        "overall": overall,
        "by_dimension": by_dimension,
        "top_profit_generators": profit,
        "top_loss_generators": loss,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "export_paths": {
            "markdown": str(md_path),
            "json": str(json_path),
        },
    }
    md_path.write_text(format_pnl_killers_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


__all__ = [
    "DIM_LABEL",
    "dimension_value",
    "format_pnl_killers_markdown",
    "format_pnl_killers_summary",
    "pnl_report_dir",
    "run_pnl_killers",
]
