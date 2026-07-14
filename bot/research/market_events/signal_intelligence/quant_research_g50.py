"""Phase G.5.0 — Quant Research Analyst (Claude nightly / on-demand, research-only)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import (
    G50_ENABLED,
    G50_MAX_RECORDS,
    G50_NIGHTLY_HOUR,
    G50_WINDOW_DAYS,
)
from bot.research.market_events.signal_intelligence.research_dataset_g50 import (
    build_research_dataset_g50,
    dataset_hash_g50,
)
from bot.research.market_events.signal_intelligence.research_prompt_g50 import (
    SYSTEM_PROMPT_G50,
    build_research_prompt_g50,
    validate_research_json_g50,
)

logger = logging.getLogger(__name__)

_TABLE = "market_events_quant_reports_g50"
_DEFAULT_TZ = "Europe/Moscow"


def _local_tz() -> ZoneInfo:
    import os
    name = os.getenv("ME_G3_REPORT_TZ", _DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _today_key() -> str:
    return datetime.now(_local_tz()).strftime("%Y-%m-%d")


def _fetch_cached_report(conn: Any, dataset_hash: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT json FROM {_TABLE} WHERE dataset_hash = ? ORDER BY created_at DESC LIMIT 1",
        (dataset_hash,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["json"])
    except (json.JSONDecodeError, TypeError):
        return None


def _already_ran_today(conn: Any, *, force: bool) -> bool:
    if force:
        return False
    from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state
    return bool(get_g3_ops_state(conn, f"g50_run_{_today_key()}"))


def _deterministic_report_g50(dataset: dict[str, Any]) -> dict[str, Any]:
    stats = dataset.get("aggregate_stats") or {}
    g4 = dataset.get("g4_factor_stats") or []
    top = [
        {"label": r.get("factor"), "evidence": f"importance {r.get('importance')}", "sample_size": r.get("sample_size")}
        for r in g4 if str(r.get("predictor_type")) == "useful"
    ][:10]
    weak = [
        {"label": r.get("factor"), "evidence": f"importance {r.get('importance')}", "sample_size": r.get("sample_size")}
        for r in g4 if str(r.get("predictor_type")) in ("weak", "negative")
    ]
    sample = int(stats.get("sample_size") or dataset.get("sample_size") or 0)
    return validate_research_json_g50({
        "top_factors": top or [{"label": "Funding", "evidence": "insufficient G4 stats", "sample_size": sample}],
        "weak_factors": weak or [{"label": "FearGreed", "evidence": "insufficient G4 stats", "sample_size": sample}],
        "overestimated_factors": [],
        "profitable_combinations": [],
        "loss_combinations": [],
        "btc_vs_alt": [],
        "symbol_specific": [],
        "new_patterns": [],
        "bad_patterns": [],
        "tomorrow_hypotheses": [{"label": "Re-run after more replay data", "sample_size": sample}],
        "research_improvements": [{"label": "Enable Claude API for deeper analysis", "sample_size": sample}],
        "confidence": 0.35 if sample < 50 else 0.55,
        "sample_size": sample,
    })


def _call_claude_research_g50(prompt: str) -> tuple[dict[str, Any], str, int, float]:
    from bot.research.market_events.signal_intelligence.claude_client_g2 import (
        ClaudeClientError,
        call_claude_json_g2,
        default_model,
        is_claude_configured,
    )

    if not is_claude_configured():
        raise ClaudeClientError("not_configured", "ANTHROPIC_API_KEY not set")

    parsed, resp = call_claude_json_g2(
        system=SYSTEM_PROMPT_G50,
        prompt=prompt,
        model=default_model(),
        label="g50_quant_research",
    )
    tokens = resp.usage.input_tokens + resp.usage.output_tokens
    return validate_research_json_g50(parsed), resp.model, tokens, resp.usage.cost_usd


def run_quant_research_g50(
    conn: Any,
    *,
    force: bool = False,
    max_records: int = G50_MAX_RECORDS,
    days: int = G50_WINDOW_DAYS,
) -> dict[str, Any]:
    if not G50_ENABLED:
        return {"status": "disabled"}

    if _already_ran_today(conn, force=force):
        latest = fetch_latest_quant_report_g50(conn)
        if latest:
            return {
                "status": "cached_daily",
                "report": latest.get("report") or {},
                "summary": latest.get("summary"),
                "created_at": latest.get("created_at"),
            }

    dataset = build_research_dataset_g50(conn, max_records=max_records, days=days)
    dhash = dataset_hash_g50(dataset)
    cached = _fetch_cached_report(conn, dhash)
    if cached and not force:
        return {"status": "cached_hash", "report": cached, "dataset_hash": dhash}

    prompt = build_research_prompt_g50(dataset)
    model = "deterministic"
    tokens = 0
    cost = 0.0
    status = "deterministic_fallback"
    try:
        from bot.research.market_events.signal_intelligence.claude_ops_g2 import (
            record_claude_failure,
            record_claude_success,
            try_consume_claude_quota,
        )
        ok, reason = try_consume_claude_quota(conn, count=True)
        if not ok:
            report = _deterministic_report_g50(dataset)
            status = f"quota_blocked:{reason}"
        else:
            try:
                report, model, tokens, cost = _call_claude_research_g50(prompt)
                from bot.research.market_events.signal_intelligence.claude_client_g2 import ClaudeUsageG2
                record_claude_success(
                    conn,
                    usage=ClaudeUsageG2(
                        input_tokens=tokens // 2,
                        output_tokens=tokens // 2,
                        cost_usd=cost,
                        latency_ms=0,
                    ),
                    model=model,
                )
                status = "claude"
            except Exception as exc:
                record_claude_failure(conn, error=str(exc), model=model)
                logger.warning("g50 claude failed: %s", exc)
                report = _deterministic_report_g50(dataset)
                status = "deterministic_fallback"
    except Exception as exc:
        logger.debug("g50 quota path skipped: %s", exc)
        report = _deterministic_report_g50(dataset)

    if not report.get("sample_size"):
        report["sample_size"] = int(dataset.get("sample_size") or 0)

    summary = _build_summary_text(report)
    now = int(time.time())
    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TABLE} (
          created_at, dataset_hash, claude_model, tokens, cost, json, summary, sample_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now, dhash, model, tokens, cost,
            json.dumps(report, ensure_ascii=False),
            summary,
            int(report.get("sample_size") or 0),
        ),
    )
    from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state
    set_g3_ops_state(conn, "last_g50_quant_ts", str(now))
    set_g3_ops_state(conn, f"g50_run_{_today_key()}", "1")

    return {
        "status": status,
        "report": report,
        "summary": summary,
        "dataset_hash": dhash,
        "model": model,
        "tokens": tokens,
        "cost": cost,
        "created_at": now,
    }


def maybe_run_quant_research_nightly_g50(conn: Any) -> bool:
    if not G50_ENABLED:
        return False
    if datetime.now(_local_tz()).hour != G50_NIGHTLY_HOUR:
        return False
    from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state
    if get_g3_ops_state(conn, f"g50_run_{_today_key()}"):
        return False
    result = run_quant_research_g50(conn, force=False)
    return result.get("status") in ("claude", "deterministic_fallback", "cached_hash", "cached_daily")


def fetch_latest_quant_report_g50(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT id, created_at, dataset_hash, claude_model, tokens, cost, json, summary, sample_size
        FROM {_TABLE} ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    if not row:
        return None
    try:
        report = json.loads(row["json"])
    except (json.JSONDecodeError, TypeError):
        report = {}
    return {
        "id": int(row["id"]),
        "created_at": int(row["created_at"]),
        "dataset_hash": row["dataset_hash"],
        "claude_model": row["claude_model"],
        "tokens": int(row["tokens"] or 0),
        "cost": float(row["cost"] or 0),
        "summary": row["summary"],
        "sample_size": int(row["sample_size"] or 0),
        "report": report,
    }


def _label_list(items: list[Any], *, key: str = "label") -> str:
    if not items:
        return "—"
    first = items[0]
    if isinstance(first, dict):
        return str(first.get(key) or first.get("factor") or first)
    return str(first)


def _combo_line(combo: dict[str, Any]) -> str:
    factors = combo.get("factors") or combo.get("label") or combo
    wr = combo.get("win_rate")
    if isinstance(factors, list):
        factors = " + ".join(str(f) for f in factors)
    line = str(factors)
    if wr is not None:
        line += f" WR {float(wr) * 100:.0f}%"
    return line


def format_quant_telegram_g50(conn: Any) -> str:
    latest = fetch_latest_quant_report_g50(conn)
    if not latest:
        result = run_quant_research_g50(conn, force=True)
        latest = {
            "sample_size": (result.get("report") or {}).get("sample_size", 0),
            "report": result.get("report") or {},
        }

    report = latest.get("report") or {}
    sample = int(report.get("sample_size") or latest.get("sample_size") or 0)
    lines = [
        "📊 Quant Research",
        "",
        "Обработано:",
        f"{sample} сигналов",
        "",
        "Самый полезный фактор",
        _label_list(report.get("top_factors") or []),
        "",
        "Самый слабый",
        _label_list(report.get("weak_factors") or []),
        "",
        "Лучшая комбинация",
        "",
        _combo_line((report.get("profitable_combinations") or [{}])[0])
        if report.get("profitable_combinations") else "—",
        "",
        "Новая гипотеза",
        "",
        _label_list(report.get("tomorrow_hypotheses") or []),
        "",
        "Подробнее:",
        "см Dashboard.",
    ]
    return "\n".join(lines)


def format_quant_report_cli_g50(conn: Any) -> str:
    latest = fetch_latest_quant_report_g50(conn)
    if not latest:
        return "G5.0 Quant Research — no reports yet. Run: quant-research"

    report = latest.get("report") or {}
    lines = [
        "G5.0 Quant Research Report",
        "",
        f"Created: {datetime.fromtimestamp(latest['created_at'], tz=timezone.utc).isoformat()}",
        f"Model: {latest.get('claude_model')}",
        f"Tokens: {latest.get('tokens')}  Cost: ${float(latest.get('cost') or 0):.4f}",
        f"Sample: {latest.get('sample_size')}",
        f"Confidence: {float(report.get('confidence') or 0):.2f}",
        "",
        "Top factors:",
    ]
    for item in (report.get("top_factors") or [])[:5]:
        lines.append(f"  - {item.get('label', item)}")
    lines.extend(["", "Weak factors:"])
    for item in (report.get("weak_factors") or [])[:5]:
        lines.append(f"  - {item.get('label', item)}")
    lines.extend(["", "Tomorrow hypotheses:"])
    for item in (report.get("tomorrow_hypotheses") or [])[:5]:
        lines.append(f"  - {item.get('label', item)}")
    if latest.get("summary"):
        lines.extend(["", "Summary:", latest["summary"]])
    return "\n".join(lines)


def _build_summary_text(report: dict[str, Any]) -> str:
    conf = float(report.get("confidence") or 0)
    return (
        f"sample={report.get('sample_size')} conf={conf:.2f} "
        f"top={_label_list(report.get('top_factors') or [])} "
        f"weak={_label_list(report.get('weak_factors') or [])} "
        f"hypothesis={_label_list(report.get('tomorrow_hypotheses') or [])}"
    )


def quant_research_dashboard_g50(conn: Any) -> dict[str, Any]:
    reports = conn.execute(
        f"""
        SELECT id, created_at, claude_model, tokens, cost, summary, sample_size
        FROM {_TABLE} ORDER BY created_at DESC LIMIT 10
        """,
    ).fetchall()
    latest = fetch_latest_quant_report_g50(conn)
    report = (latest or {}).get("report") or {}
    return {
        "tab": "Quant Research",
        "latest": latest,
        "recent_reports": [dict(r) for r in reports],
        "top_factors": report.get("top_factors") or [],
        "weak_factors": report.get("weak_factors") or [],
        "new_hypotheses": report.get("tomorrow_hypotheses") or [],
        "recommendations": report.get("research_improvements") or [],
        "new_patterns": report.get("new_patterns") or [],
        "claude_cost_total": sum(float(r["cost"] or 0) for r in reports),
    }
