"""Phase G.5.0 — Quant Research Analyst (Claude nightly / on-demand, research-only)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.claude_json_parser_g501 import (
    extract_json_from_claude_text,
    repair_json,
)
from bot.research.market_events.signal_intelligence.config import (
    G50_ENABLED,
    G50_MAX_RECORDS,
    G50_NIGHTLY_HOUR,
    G50_WINDOW_DAYS,
)
from bot.research.market_events.signal_intelligence.deterministic_research_g501 import (
    build_deterministic_research_g501,
    compute_research_score_g501,
)
from bot.research.market_events.signal_intelligence.research_dataset_g50 import (
    build_research_dataset_g50,
    dataset_hash_g50,
)
from bot.research.market_events.signal_intelligence.research_prompt_g50 import (
    SYSTEM_PROMPT_G50,
    build_research_prompt_g50,
    estimate_prompt_tokens,
    validate_research_json_g50,
)

logger = logging.getLogger(__name__)

_TABLE = "market_events_quant_reports_g50"
_DEBUG_OPS_KEY = "g50_last_debug"
_MAX_JSON_RETRIES = 2


def _local_tz() -> ZoneInfo:
    import os
    name = os.getenv("ME_G3_REPORT_TZ", _DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


_DEFAULT_TZ = "Europe/Moscow"


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


def _save_debug(conn: Any, debug: dict[str, Any]) -> None:
    from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state
    set_g3_ops_state(conn, _DEBUG_OPS_KEY, json.dumps(debug, ensure_ascii=False, default=str))


def _load_debug(conn: Any) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state
    raw = get_g3_ops_state(conn, _DEBUG_OPS_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _call_claude_research_g50(
    conn: Any,
    prompt: str,
    *,
    sample_size: int,
) -> tuple[dict[str, Any] | None, str, int, float, dict[str, Any]]:
    from bot.research.market_events.signal_intelligence.claude_client_g2 import (
        ClaudeClientError,
        call_claude_g2,
        default_model,
        is_claude_configured,
    )

    debug: dict[str, Any] = {
        "prompt": prompt,
        "prompt_tokens_est": estimate_prompt_tokens(prompt),
        "system": SYSTEM_PROMPT_G50,
        "errors": [],
        "retries": 0,
        "responses": [],
        "extracted_json": None,
        "latency_ms": 0,
        "tokens": 0,
    }

    if not is_claude_configured():
        debug["errors"].append("ANTHROPIC_API_KEY not set")
        raise ClaudeClientError("not_configured", "ANTHROPIC_API_KEY not set")

    model = default_model()
    total_tokens = 0
    total_cost = 0.0
    last_raw = ""
    user_prompt = prompt

    for attempt in range(_MAX_JSON_RETRIES + 1):
        if attempt > 0:
            debug["retries"] = attempt
            user_prompt = prompt + "\n\nReturn ONLY valid JSON. No markdown. No prose."

        try:
            resp = call_claude_g2(
                system=SYSTEM_PROMPT_G50 + "\nOutput ONLY valid JSON.",
                user_content=user_prompt,
                model=model,
                label="g50_quant_research",
            )
        except ClaudeClientError as exc:
            debug["errors"].append(str(exc))
            _save_debug(conn, debug)
            raise

        last_raw = resp.text
        total_tokens += resp.usage.input_tokens + resp.usage.output_tokens
        total_cost += resp.usage.cost_usd
        debug["latency_ms"] = int(resp.usage.latency_ms)
        debug["tokens"] = total_tokens
        debug["responses"].append(last_raw[:4000])

        parsed, err = extract_json_from_claude_text(last_raw)
        if not parsed:
            repaired = repair_json(last_raw)
            if repaired:
                parsed = repaired
                err = None

        if parsed:
            parsed = validate_research_json_g50(parsed)
            if not parsed.get("sample_size"):
                parsed["sample_size"] = sample_size
            parsed["research_score"] = compute_research_score_g501(
                "claude", parsed.get("confidence", 0.5), parsed.get("sample_size", sample_size),
                retries=attempt,
            )
            debug["extracted_json"] = parsed
            _save_debug(conn, debug)
            return parsed, model, total_tokens, total_cost, debug

        debug["errors"].append(err or f"parse failed attempt {attempt + 1}")

    debug["raw_response"] = last_raw[:12000]
    _save_debug(conn, debug)
    return None, model, total_tokens, total_cost, debug


def _persist_report(
    conn: Any,
    *,
    report: dict[str, Any],
    dhash: str,
    model: str,
    tokens: int,
    cost: float,
    summary: str,
    raw_response: str | None,
    research_score: float,
    missing_info: list[Any],
    debug: dict[str, Any],
) -> int:
    now = int(time.time())
    missing_json = json.dumps(missing_info, ensure_ascii=False)
    debug_json = json.dumps(debug, ensure_ascii=False, default=str)
    return insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TABLE} (
          created_at, dataset_hash, claude_model, tokens, cost, json, summary, sample_size,
          raw_response, research_score, missing_info_json, debug_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now, dhash, model, tokens, cost,
            json.dumps(report, ensure_ascii=False),
            summary,
            int(report.get("sample_size") or 0),
            raw_response,
            research_score,
            missing_json,
            debug_json,
        ),
    )


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
                "research_score": latest.get("research_score"),
                "created_at": latest.get("created_at"),
            }

    dataset = build_research_dataset_g50(conn, max_records=max_records, days=days)
    dhash = dataset_hash_g50(dataset)
    sample_size = int(dataset.get("sample_size") or 0)

    cached = _fetch_cached_report(conn, dhash)
    if cached and not force:
        return {"status": "cached_hash", "report": cached, "dataset_hash": dhash}

    prompt = build_research_prompt_g50(dataset, conn=conn)
    model = "deterministic"
    tokens = 0
    cost = 0.0
    status = "deterministic_fallback"
    raw_response: str | None = None
    debug: dict[str, Any] = {"prompt": prompt, "prompt_tokens_est": estimate_prompt_tokens(prompt)}

    try:
        from bot.research.market_events.signal_intelligence.claude_ops_g2 import (
            record_claude_failure,
            record_claude_success,
            try_consume_claude_quota,
        )
        ok, reason = try_consume_claude_quota(conn, count=True)
        if not ok:
            report = build_deterministic_research_g501(conn, dataset=dataset)
            status = f"quota_blocked:{reason}"
        else:
            try:
                parsed, model, tokens, cost, debug = _call_claude_research_g50(
                    conn, prompt, sample_size=sample_size,
                )
                if parsed:
                    report = parsed
                    status = "claude"
                    from bot.research.market_events.signal_intelligence.claude_client_g2 import ClaudeUsageG2
                    record_claude_success(
                        conn,
                        usage=ClaudeUsageG2(
                            input_tokens=tokens // 2,
                            output_tokens=tokens // 2,
                            cost_usd=cost,
                            latency_ms=debug.get("latency_ms", 0),
                        ),
                        model=model,
                    )
                else:
                    raw_response = debug.get("raw_response")
                    report = build_deterministic_research_g501(conn, dataset=dataset)
                    report["parse_fallback"] = True
                    if raw_response:
                        report["research_score"] = compute_research_score_g501(
                            "claude", report.get("confidence", 0.5), sample_size,
                            retries=debug.get("retries", 0), raw_only=True,
                        )
                    status = "claude_raw_fallback"
                    record_claude_failure(conn, error="invalid_json_after_retries", model=model)
            except Exception as exc:
                record_claude_failure(conn, error=str(exc), model=model)
                logger.warning("g50 claude failed: %s", exc)
                report = build_deterministic_research_g501(conn, dataset=dataset)
                status = "deterministic_fallback"
                debug["errors"] = debug.get("errors", []) + [str(exc)]
    except Exception as exc:
        logger.debug("g50 quota path skipped: %s", exc)
        report = build_deterministic_research_g501(conn, dataset=dataset)
        debug["errors"] = [str(exc)]

    if not report.get("sample_size"):
        report["sample_size"] = sample_size

    research_score = float(
        report.get("research_score")
        or compute_research_score_g501(
            status, float(report.get("confidence") or 0.5), sample_size,
            retries=int(debug.get("retries") or 0),
            raw_only=bool(raw_response),
        )
    )
    report["research_score"] = research_score
    missing_info = report.get("missing_info") or []

    summary = _build_summary_text(report, research_score=research_score, status=status)
    _save_debug(conn, debug)

    now = int(time.time())
    _persist_report(
        conn,
        report=report,
        dhash=dhash,
        model=model,
        tokens=tokens,
        cost=cost,
        summary=summary,
        raw_response=raw_response,
        research_score=research_score,
        missing_info=missing_info,
        debug=debug,
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
        "research_score": research_score,
        "created_at": now,
        "debug": debug,
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
    return result.get("status") in ("claude", "deterministic_fallback", "claude_raw_fallback", "cached_hash", "cached_daily")


def fetch_latest_quant_report_g50(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT id, created_at, dataset_hash, claude_model, tokens, cost, json, summary,
               sample_size, raw_response, research_score, missing_info_json, debug_json
        FROM {_TABLE} ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    if not row:
        return None
    try:
        report = json.loads(row["json"])
    except (json.JSONDecodeError, TypeError):
        report = {}
    missing_info: list[Any] = []
    if row["missing_info_json"]:
        try:
            missing_info = json.loads(row["missing_info_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": int(row["id"]),
        "created_at": int(row["created_at"]),
        "dataset_hash": row["dataset_hash"],
        "claude_model": row["claude_model"],
        "tokens": int(row["tokens"] or 0),
        "cost": float(row["cost"] or 0),
        "summary": row["summary"],
        "sample_size": int(row["sample_size"] or 0),
        "raw_response": row["raw_response"],
        "research_score": float(row["research_score"] or 0) if row["research_score"] is not None else None,
        "missing_info": missing_info,
        "debug_json": row["debug_json"],
        "report": report,
    }


def format_quant_debug_g50(conn: Any) -> str:
    debug = _load_debug(conn)
    latest = fetch_latest_quant_report_g50(conn)
    if latest and not debug:
        if latest.get("debug_json"):
            try:
                debug = json.loads(latest["debug_json"]) if isinstance(latest.get("debug_json"), str) else {}
            except (json.JSONDecodeError, TypeError):
                debug = {}
        elif latest.get("raw_response"):
            debug = {"raw_response": latest["raw_response"]}

    lines = ["G5.0 Quant Research Debug", ""]
    if not debug and not latest:
        return "G5.0 Quant Debug — no runs yet. Try: quant-research"

    lines.extend([
        "Prompt tokens (est)",
        str(debug.get("prompt_tokens_est", "—")),
        "",
        "Prompt",
        (debug.get("prompt") or "—")[:2000],
        "",
        "Response",
        (debug.get("raw_response") or (debug.get("responses") or ["—"])[-1] if debug.get("responses") else "—")[:2000],
        "",
        "Extracted JSON",
        json.dumps(debug.get("extracted_json") or (latest or {}).get("report"), indent=2, ensure_ascii=False)[:2000],
        "",
        "Errors",
        "\n".join(debug.get("errors") or []) or "—",
        "",
        "Retries",
        str(debug.get("retries", 0)),
        "",
        "Tokens",
        str(debug.get("tokens") or (latest or {}).get("tokens", "—")),
        "",
        "Latency ms",
        str(debug.get("latency_ms", "—")),
        "",
        "Research score",
        str((latest or {}).get("research_score") or debug.get("extracted_json", {}).get("research_score", "—")),
    ])
    return "\n".join(lines)


def format_quant_debug_telegram_g50(conn: Any) -> str:
    latest = fetch_latest_quant_report_g50(conn)
    debug = _load_debug(conn)
    score = (latest or {}).get("research_score")
    source = (latest or {}).get("claude_model") or "—"
    label = "Claude" if source not in ("deterministic", "—") else "Fallback"
    lines = [
        "Quant Research Debug",
        "",
        "Research score",
        f"{label} {score}/10" if score else "—",
        "",
        "Tokens",
        str(debug.get("tokens") or (latest or {}).get("tokens", "—")),
        "",
        "Retries",
        str(debug.get("retries", 0)),
        "",
        "Errors",
        "\n".join(debug.get("errors") or [])[:500] or "—",
        "",
        "Missing info",
    ]
    missing = (latest or {}).get("missing_info") or []
    if missing:
        for item in missing[:5]:
            lines.append(f"- {item if isinstance(item, str) else item.get('label', item)}")
    else:
        lines.append("—")
    lines.extend(["", "Full debug: quant-debug CLI"])
    return "\n".join(lines)


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
            "research_score": result.get("research_score"),
        }

    report = latest.get("report") or {}
    sample = int(report.get("sample_size") or latest.get("sample_size") or 0)
    score = latest.get("research_score") or report.get("research_score")
    score_line = f"{score}/10" if score else "—"

    lines = [
        "📊 Quant Research",
        "",
        "Обработано:",
        f"{sample} сигналов",
        "",
        "Research score",
        score_line,
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
        f"Research score: {latest.get('research_score') or report.get('research_score')}/10",
        "",
        "Top factors:",
    ]
    for item in (report.get("top_factors") or [])[:5]:
        lines.append(f"  - {item.get('label', item)}")
    lines.extend(["", "Weak factors:"])
    for item in (report.get("weak_factors") or [])[:5]:
        lines.append(f"  - {item.get('label', item)}")
    lines.extend(["", "Missing info:"])
    for item in (report.get("missing_info") or latest.get("missing_info") or [])[:5]:
        lines.append(f"  - {item if isinstance(item, str) else item.get('label', item)}")
    lines.extend(["", "Tomorrow hypotheses:"])
    for item in (report.get("tomorrow_hypotheses") or [])[:5]:
        lines.append(f"  - {item.get('label', item)}")
    if latest.get("summary"):
        lines.extend(["", "Summary:", latest["summary"]])
    return "\n".join(lines)


def _build_summary_text(report: dict[str, Any], *, research_score: float, status: str) -> str:
    conf = float(report.get("confidence") or 0)
    return (
        f"sample={report.get('sample_size')} conf={conf:.2f} score={research_score:.1f} "
        f"status={status} top={_label_list(report.get('top_factors') or [])} "
        f"weak={_label_list(report.get('weak_factors') or [])}"
    )


def quant_research_dashboard_g50(conn: Any) -> dict[str, Any]:
    reports = conn.execute(
        f"""
        SELECT id, created_at, claude_model, tokens, cost, summary, sample_size, research_score
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
        "missing_info": report.get("missing_info") or latest.get("missing_info") if latest else [],
        "research_score": latest.get("research_score") if latest else None,
        "claude_cost_total": sum(float(r["cost"] or 0) for r in reports),
    }
