"""Phase G.2 — Claude ops: daily quota, health counters, fail-safe bookkeeping."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.signal_intelligence.config import G2_DAILY_REQUEST_LIMIT
from bot.research.market_events.signal_intelligence.claude_client_g2 import (
    ClaudeUsageG2,
    default_model,
)

_OPS_TABLE = "market_events_g2_ops_state"

AI_STATUS_OK = "OK"
AI_STATUS_SKIPPED = "AI_SKIPPED"
AI_STATUS_CACHED = "CACHED"
AI_STATUS_RATE_LIMITED = "RATE_LIMITED"
AI_STATUS_DETERMINISTIC = "DETERMINISTIC"


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_ops(conn: Any, key: str, default: str = "") -> str:
    row = conn.execute(
        f"SELECT value FROM {_OPS_TABLE} WHERE key = ?",
        (key,),
    ).fetchone()
    return str(row["value"]) if row else default


def _set_ops(conn: Any, key: str, value: str) -> None:
    now = int(time.time())
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {_OPS_TABLE} (key, value, updated_at)
        VALUES (?, ?, ?)
        """,
        (key, value, now),
    )


def _ensure_quota_day(conn: Any) -> None:
    today = _utc_day()
    if _get_ops(conn, "quota_day") != today:
        _set_ops(conn, "quota_day", today)
        _set_ops(conn, "requests_today", "0")
        _set_ops(conn, "input_tokens_today", "0")
        _set_ops(conn, "output_tokens_today", "0")
        _set_ops(conn, "cost_usd_today", "0")


def get_daily_usage(conn: Any) -> dict[str, Any]:
    _ensure_quota_day(conn)
    return {
        "quota_day": _get_ops(conn, "quota_day", _utc_day()),
        "requests_today": int(_get_ops(conn, "requests_today", "0")),
        "limit": G2_DAILY_REQUEST_LIMIT,
        "input_tokens_today": int(_get_ops(conn, "input_tokens_today", "0")),
        "output_tokens_today": int(_get_ops(conn, "output_tokens_today", "0")),
        "cost_usd_today": float(_get_ops(conn, "cost_usd_today", "0")),
    }


def can_make_claude_request(conn: Any) -> tuple[bool, str | None]:
    _ensure_quota_day(conn)
    used = int(_get_ops(conn, "requests_today", "0"))
    if used >= G2_DAILY_REQUEST_LIMIT:
        return False, f"daily_limit {used}/{G2_DAILY_REQUEST_LIMIT}"
    return True, None


def try_consume_claude_quota(conn: Any, *, count: bool = True) -> tuple[bool, str | None]:
    """Reserve one daily slot before an API call."""
    if not count:
        return True, None
    ok, reason = can_make_claude_request(conn)
    if not ok:
        return False, reason
    used = int(_get_ops(conn, "requests_today", "0"))
    _set_ops(conn, "requests_today", str(used + 1))
    return True, None


def record_claude_success(
    conn: Any,
    *,
    usage: ClaudeUsageG2,
    model: str | None = None,
    count_tokens: bool = True,
) -> None:
    _ensure_quota_day(conn)
    now = int(time.time())
    model = model or default_model()
    _set_ops(conn, "last_success_at", str(now))
    _set_ops(conn, "last_success_model", model)
    if count_tokens:
        in_tok = int(_get_ops(conn, "input_tokens_today", "0")) + usage.input_tokens
        out_tok = int(_get_ops(conn, "output_tokens_today", "0")) + usage.output_tokens
        cost = float(_get_ops(conn, "cost_usd_today", "0")) + usage.cost_usd
        _set_ops(conn, "input_tokens_today", str(in_tok))
        _set_ops(conn, "output_tokens_today", str(out_tok))
        _set_ops(conn, "cost_usd_today", f"{cost:.6f}")


def record_claude_failure(
    conn: Any,
    *,
    error: str,
    model: str | None = None,
) -> None:
    now = int(time.time())
    model = model or default_model()
    _set_ops(conn, "last_failure_at", str(now))
    _set_ops(conn, "last_failure_error", error[:500])
    _set_ops(conn, "last_failure_model", model)


def format_ts(ts_raw: str) -> str:
    if not ts_raw:
        return "—"
    try:
        ts = int(ts_raw)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError):
        return ts_raw


def format_claude_health_report(conn: Any) -> str:
    usage = get_daily_usage(conn)
    model = default_model()
    last_ok = format_ts(_get_ops(conn, "last_success_at"))
    last_ok_model = _get_ops(conn, "last_success_model") or "—"
    last_fail = format_ts(_get_ops(conn, "last_failure_at"))
    last_fail_err = _get_ops(conn, "last_failure_error") or "—"
    last_fail_model = _get_ops(conn, "last_failure_model") or "—"

    lines = [
        "CLAUDE HEALTH (G.2)",
        "",
        f"Model: {model}",
        f"Daily limit: {usage['limit']} requests",
        "",
        "Today:",
        f"  Requests: {usage['requests_today']}/{usage['limit']}",
        f"  Input tokens: {usage['input_tokens_today']}",
        f"  Output tokens: {usage['output_tokens_today']}",
        f"  Est. cost: ${usage['cost_usd_today']:.4f}",
        "",
        f"Last success: {last_ok}",
        f"  Model: {last_ok_model}",
        "",
        f"Last failure: {last_fail}",
        f"  Model: {last_fail_model}",
        f"  Error: {last_fail_err}",
    ]
    return "\n".join(lines)


def run_claude_test() -> tuple[int, str]:
    """Ping Anthropic API — does not consume daily research quota."""
    from bot.research.market_events.signal_intelligence.claude_client_g2 import (
        ClaudeClientError,
        call_claude_json_g2,
        is_claude_configured,
    )

    model = default_model()
    lines = [
        "CLAUDE TEST (G.2)",
        "",
        f"API key configured: {'yes' if is_claude_configured() else 'no'}",
        f"Model: {model}",
    ]

    if not is_claude_configured():
        lines.extend(["", "API: NOT AVAILABLE", "Set ANTHROPIC_API_KEY in .env"])
        return 1, "\n".join(lines)

    try:
        parsed, resp = call_claude_json_g2(
            system='Reply with JSON only: {"ok": true, "message": "pong"}',
            prompt='{"ping": true}',
            model=model,
            label="claude_test",
        )
        lines.extend([
            "",
            "API: AVAILABLE",
            f"Latency: {resp.usage.latency_ms:.0f} ms",
            f"Input tokens: {resp.usage.input_tokens}",
            f"Output tokens: {resp.usage.output_tokens}",
            f"Est. cost: ${resp.usage.cost_usd:.6f}",
            f"Response: {json.dumps(parsed, ensure_ascii=False)}",
        ])
        return 0, "\n".join(lines)
    except ClaudeClientError as exc:
        lines.extend(["", "API: ERROR", f"Kind: {exc.kind}", f"Error: {exc}"])
        return 1, "\n".join(lines)
    except Exception as exc:
        lines.extend(["", "API: ERROR", f"Error: {exc}"])
        return 1, "\n".join(lines)
