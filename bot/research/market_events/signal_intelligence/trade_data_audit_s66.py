"""S66 — Trade Data Completeness Audit (research-only).

Measure fill rates for attribution fields used by PnL / filter / long-short reports.
Also documents open-time persistence expectations for new trades.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from bot.research.market_events.signal_intelligence.drift_analyzer_s622 import (
    _enrich_from_snapshot_json,
)
from bot.research.market_events.signal_intelligence.feature_lab_s59 import load_lab_trades
from bot.research.market_events.signal_intelligence.pnl_killers_s64 import (
    _entry_reason_key,
    pnl_report_dir,
)
from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
    WEEKDAYS,
    _enrich_decisions,
    _safe_float,
    _trade_ts,
)

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def _session_from_hour(hour: int | None) -> str | None:
    if hour is None:
        return None
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    if 0 <= h < 8:
        return "Asia"
    if 8 <= h < 13:
        return "London"
    if 13 <= h < 21:
        return "NewYork"
    if 0 <= h <= 23:
        return "Offhours"
    return None


def _json_blob(r: dict[str, Any]) -> dict[str, Any]:
    raw = r.get("snapshot_json")
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _from_json(r: dict[str, Any], *keys: str) -> Any:
    blob = _json_blob(r)
    feat = blob.get("features") if isinstance(blob.get("features"), dict) else {}
    for k in keys:
        if r.get(k) is not None and r.get(k) != "":
            return r.get(k)
        if blob.get(k) is not None and blob.get(k) != "":
            return blob.get(k)
        if feat.get(k) is not None and feat.get(k) != "":
            return feat.get(k)
    return None


def _filled(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and v.strip() in ("", "—", "unknown", "None", "null"):
        return False
    return True


def _extractors() -> list[tuple[str, Callable[[dict[str, Any]], Any]]]:
    """Canonical attribution fields → value extractor after enrichment."""

    def coin(r: dict[str, Any]) -> Any:
        return (
            str(r.get("symbol") or "").upper().replace("USDT", "").strip()
            or _from_json(r, "coin")
        )

    def strategy(r: dict[str, Any]) -> Any:
        return r.get("s40_signal_type") or _from_json(r, "strategy", "s40_signal_type")

    def entry_reason(r: dict[str, Any]) -> Any:
        v = _from_json(r, "entry_reason")
        if _filled(v):
            return v
        why = _entry_reason_key(r)
        return why if why != "unknown" else None

    def confidence(r: dict[str, Any]) -> Any:
        return _safe_float(
            _from_json(r, "decision_confidence", "confidence", "ai_score")
        )

    def ai_score(r: dict[str, Any]) -> Any:
        return _safe_float(_from_json(r, "ai_score") if _from_json(r, "ai_score") is not None else r.get("ai_score"))

    def funding(r: dict[str, Any]) -> Any:
        return _safe_float(r.get("funding") if r.get("funding") is not None else _from_json(r, "funding"))

    def oi(r: dict[str, Any]) -> Any:
        return _safe_float(
            r.get("oi_delta")
            if r.get("oi_delta") is not None
            else _from_json(r, "open_interest", "oi_delta", "oi")
        )

    def regime(r: dict[str, Any]) -> Any:
        return r.get("market_regime") or _from_json(r, "market_regime", "regime")

    def volatility(r: dict[str, Any]) -> Any:
        return _safe_float(r.get("volatility") if r.get("volatility") is not None else _from_json(r, "volatility"))

    def atr(r: dict[str, Any]) -> Any:
        return _safe_float(r.get("atr") if r.get("atr") is not None else _from_json(r, "atr"))

    def ema_trend(r: dict[str, Any]) -> Any:
        v = _safe_float(_from_json(r, "ema_trend", "trend"))
        if v is not None:
            return v
        e20 = _safe_float(r.get("ema20") if r.get("ema20") is not None else _from_json(r, "ema20"))
        e50 = _safe_float(r.get("ema50") if r.get("ema50") is not None else _from_json(r, "ema50"))
        if e20 is not None and e50 is not None:
            return e20 - e50
        return None

    def session(r: dict[str, Any]) -> Any:
        v = _from_json(r, "session")
        if _filled(v):
            return v
        hour = r.get("hour")
        try:
            hour_i = int(hour) if hour is not None else None
        except (TypeError, ValueError):
            hour_i = None
        if hour_i is None:
            ts = _trade_ts(r)
            if ts > 0:
                from datetime import datetime, timezone
                hour_i = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        return _session_from_hour(hour_i)

    def weekday(r: dict[str, Any]) -> Any:
        try:
            w = int(r.get("weekday"))
            if 0 <= w <= 6:
                return WEEKDAYS[w]
        except (TypeError, ValueError, IndexError):
            pass
        return _from_json(r, "weekday")

    def hour(r: dict[str, Any]) -> Any:
        try:
            h = int(r.get("hour"))
            if 0 <= h <= 23:
                return h
        except (TypeError, ValueError):
            pass
        return _from_json(r, "hour")

    def news_score(r: dict[str, Any]) -> Any:
        return _safe_float(r.get("news_score") if r.get("news_score") is not None else _from_json(r, "news_score"))

    def fear_greed(r: dict[str, Any]) -> Any:
        return _safe_float(r.get("fear_greed") if r.get("fear_greed") is not None else _from_json(r, "fear_greed"))

    def btc_dominance(r: dict[str, Any]) -> Any:
        return _safe_float(_from_json(r, "btc_dominance"))

    def regime_version(r: dict[str, Any]) -> Any:
        return _from_json(r, "market_regime_version")

    def direction(r: dict[str, Any]) -> Any:
        d = str(r.get("direction") or "").upper().strip()
        return d if d in ("LONG", "SHORT") else None

    return [
        ("direction", direction),
        ("coin", coin),
        ("strategy", strategy),
        ("entry_reason", entry_reason),
        ("confidence", confidence),
        ("ai_score", ai_score),
        ("funding", funding),
        ("oi", oi),
        ("regime", regime),
        ("volatility", volatility),
        ("atr", atr),
        ("ema_trend", ema_trend),
        ("session", session),
        ("weekday", weekday),
        ("hour", hour),
        ("news_score", news_score),
        ("fear_greed", fear_greed),
        ("btc_dominance", btc_dominance),
        ("market_regime_version", regime_version),
    ]


def audit_fill_rates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = len(rows)
    out: list[dict[str, Any]] = []
    for name, fn in _extractors():
        filled = 0
        for r in rows:
            try:
                if _filled(fn(r)):
                    filled += 1
            except Exception:
                continue
        pct = round(100.0 * filled / n, 2) if n else 0.0
        out.append({
            "field": name,
            "filled": filled,
            "missing": n - filled,
            "fill_pct": pct,
            "status": "ok" if pct >= 95 else ("warn" if pct >= 50 else "critical"),
        })
    return out


def format_data_quality_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Trade Data Completeness (S66)",
        "",
        f"_trades={report.get('n_trades')} elapsed={report.get('elapsed_sec')}s_",
        "",
        "Fill rates after S56 + S58 + snapshot_json enrichment (same path as PnL reports).",
        "New opens persist attribution via S55 `features_json` / S58 `inputs_json` / S56 `snapshot_json`.",
        "",
        f"## Trades",
        "",
        f"**{report.get('n_trades')}**",
        "",
        "----------------",
        "",
        "| Field | Fill % | Filled | Missing | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for row in report.get("fields") or []:
        lines.append(
            f"| {row.get('field')} | {row.get('fill_pct')}% | {row.get('filled')} | "
            f"{row.get('missing')} | {row.get('status')} |"
        )
    lines.extend(["", "## Summary", ""])
    summary = report.get("summary") or {}
    lines.append(f"- Fields ≥95%: **{summary.get('ok')}**")
    lines.append(f"- Fields 50–95%: **{summary.get('warn')}**")
    lines.append(f"- Fields <50%: **{summary.get('critical')}**")
    if summary.get("worst"):
        lines.append(
            f"- Worst field: **{summary['worst'].get('field')}** "
            f"({summary['worst'].get('fill_pct')}%)"
        )
    lines.extend([
        "",
        "## Open-time attribution (S66)",
        "",
        "On each paper open, `build_entry_features` now records:",
        "",
        "- direction, coin/symbol, strategy, hour, weekday, session",
        "- confidence / ai_score, funding, OI / oi_delta, ATR, volatility, trend / ema_trend",
        "- news_score, fear_greed, btc_dominance (when g3 has it)",
        "- market_regime + market_regime_version (S57)",
        "- entry_reason stamped into S58 `inputs_json` from why_opened tags",
        "",
        "Close snapshots copy these into `snapshot_json` for research audits.",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def format_data_quality_summary(report: dict[str, Any]) -> str:
    lines = [
        "S66 Trade Data Completeness Audit",
        f"  trades={report.get('n_trades')} elapsed={report.get('elapsed_sec')}s",
        "",
        "Trades",
        f"  {report.get('n_trades')}",
        "----------------",
    ]
    for row in report.get("fields") or []:
        lines.append(str(row.get("field")))
        lines.append(f"  {row.get('fill_pct')}%")
        lines.append("  filled")
        lines.append("")
    for k, p in (report.get("export_paths") or {}).items():
        lines.append(f"  {k}: {p}")
    return "\n".join(lines).rstrip()


def run_trade_data_audit(
    conn: Any,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rows = load_lab_trades(conn)
    _enrich_decisions(conn, rows)
    _enrich_from_snapshot_json(rows)
    closed = [r for r in rows if r.get("pnl_usd") is not None]

    fields = audit_fill_rates(closed)
    ok = sum(1 for f in fields if f.get("status") == "ok")
    warn = sum(1 for f in fields if f.get("status") == "warn")
    critical = sum(1 for f in fields if f.get("status") == "critical")
    worst = min(fields, key=lambda f: float(f.get("fill_pct") or 0.0)) if fields else None

    out_dir = Path(report_dir) if report_dir else pnl_report_dir(_repo_root())
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "data_quality.md"
    json_path = out_dir / "data_quality.json"

    report: dict[str, Any] = {
        "ok": True,
        "stage": "S66",
        "n_trades": len(closed),
        "fields": fields,
        "summary": {
            "ok": ok,
            "warn": warn,
            "critical": critical,
            "worst": worst,
        },
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "export_paths": {
            "markdown": str(md_path),
            "json": str(json_path),
        },
    }
    md_path.write_text(format_data_quality_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


__all__ = [
    "audit_fill_rates",
    "format_data_quality_markdown",
    "format_data_quality_summary",
    "run_trade_data_audit",
]
