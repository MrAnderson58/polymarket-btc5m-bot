"""Structured Telegram alert sections — EVENT / MARKET / TELEGRAM / NEWS / HISTORY."""

from __future__ import annotations

import json
import statistics
import time
from typing import Any

from bot.research.market_events.alert_config import alert_locale


def _col(row: Any, name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def _t(key: str, **kwargs: Any) -> str:
    loc = alert_locale()
    table = _STRINGS.get(loc, _STRINGS["en"])
    text = table.get(key, _STRINGS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def _fmt_return(val: float | None) -> str:
    if val is None:
        return "n/a"
    return f"{val:+.1f}%"


def _fmt_window(seconds: int | None, loc: str | None = None) -> str:
    if not seconds:
        return "?"
    loc = loc or alert_locale()
    minutes = max(1, round(seconds / 60))
    if loc == "ru":
        return f"за {minutes} {_ru_minutes(minutes)}"
    if minutes == 1:
        return "in 1 minute"
    return f"in {minutes} minutes"


def _ru_minutes(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "минут"
    r = n % 10
    if r == 1:
        return "минуту"
    if 2 <= r <= 4:
        return "минуты"
    return "минут"


def _time_ago(seconds: int) -> str:
    loc = alert_locale()
    if seconds >= 86400:
        d = seconds // 86400
        if loc == "ru":
            return f"{d} {_ru_days(d)} назад"
        return f"{d}d ago"
    if seconds >= 3600:
        h = seconds // 3600
        if loc == "ru":
            return f"{h} {_ru_hours(h)} назад"
        return f"{h}h ago"
    m = max(1, seconds // 60)
    if loc == "ru":
        return f"{m} {_ru_minutes(m)} назад"
    return f"{m}m ago"


def _ru_hours(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "часов"
    r = n % 10
    if r == 1:
        return "час"
    if 2 <= r <= 4:
        return "часа"
    return "часов"


def _ru_days(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "дней"
    r = n % 10
    if r == 1:
        return "день"
    if 2 <= r <= 4:
        return "дня"
    return "дней"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    sec = int(seconds)
    m, s = divmod(sec, 60)
    if alert_locale() == "ru":
        if m and s:
            return f"{m}m {s}s"
        if m:
            return f"{m}m"
        return f"{s}s"
    if m and s:
        return f"{m}m {s}s"
    if m:
        return f"{m}m"
    return f"{s}s"


def _classification_label(raw: str | None) -> str:
    mapping = {
        "ASSET_SPECIFIC": "asset-specific anomaly",
        "MARKET_WIDE": "market-wide move",
        "SECTOR_ROTATION": "sector rotation",
        "INDEX_LEAD": "index-led move",
        "UNKNOWN": "unclassified",
    }
    return mapping.get((raw or "UNKNOWN").upper(), (raw or "unknown").lower().replace("_", " "))


def _fetch_post_preview(post_id: str | int) -> str | None:
    try:
        from bot.research.futures_agent.db import agent_connection

        with agent_connection() as agent:
            row = agent.execute(
                "SELECT raw_text FROM futures_agent_trader_posts WHERE id = ?",
                (int(post_id),),
            ).fetchone()
            if row and row["raw_text"]:
                return (row["raw_text"] or "").strip().replace("\n", " ")[:160]
    except Exception:
        pass
    return None


def fetch_telegram_line(conn: Any, *, event_id: int, event_ts: int) -> str:
    rows = conn.execute(
        """
        SELECT context_type, source, context_ts, time_delta_seconds,
               relevance_score, context_json
        FROM market_event_context
        WHERE event_id = ?
          AND context_type IN (
            'TELEGRAM_SIGNAL', 'TRADER_THESIS', 'MARKET_COMMENTARY', 'NEWS'
          )
        ORDER BY relevance_score DESC, context_ts DESC
        LIMIT 3
        """,
        (event_id,),
    ).fetchall()
    if not rows:
        return _t("no_telegram")

    best = rows[0]
    meta = json.loads(best["context_json"] or "{}")
    post_id = meta.get("post_id")
    preview = _fetch_post_preview(post_id) if post_id else None
    ago_sec = abs(int(best["time_delta_seconds"] or (event_ts - int(best["context_ts"]))))
    prefix = _time_ago(ago_sec)
    if preview:
        return f"{prefix}: {preview}"
    ctype = best["context_type"].replace("_", " ").lower()
    return f"{prefix}: [{ctype}] {best['source']}"


def fetch_news_line(conn: Any, *, event_id: int, symbol: str) -> str:
    rows = conn.execute(
        """
        SELECT context_json, context_ts, source
        FROM market_event_context
        WHERE event_id = ? AND context_type = 'NEWS'
        ORDER BY context_ts DESC LIMIT 1
        """,
        (event_id,),
    ).fetchall()
    if not rows:
        return _t("news_none", symbol=symbol)
    meta = json.loads(rows[0]["context_json"] or "{}")
    post_id = meta.get("post_id")
    preview = _fetch_post_preview(post_id) if post_id else None
    if preview:
        return preview
    return _t("news_linked", source=rows[0]["source"])


def fetch_history_block(
    conn: Any,
    *,
    symbol: str,
    direction: str,
    return_pct: float,
    exclude_event_id: int,
    reversal_threshold_pct: float = 1.5,
) -> str:
    min_abs = max(1.0, abs(return_pct) * 0.65)
    rows = conn.execute(
        """
        SELECT e.id, e.return_pct, p.confirmed_reversal, p.confirm_latency_sec,
               p.path_from_extreme_json
        FROM market_events e
        LEFT JOIN market_events_pending_shocks p ON p.event_id = e.id
        WHERE e.symbol = ? AND e.id != ?
          AND ABS(e.return_pct) >= ?
        ORDER BY e.event_ts DESC
        LIMIT 200
        """,
        (symbol, exclude_event_id, min_abs),
    ).fetchall()
    if not rows:
        return _t("history_none")

    total = len(rows)
    reversal_hits = 0
    latencies: list[int] = []

    for r in rows:
        path = json.loads(r["path_from_extreme_json"] or "null") if r["path_from_extreme_json"] else None
        reclaim = path.get("reclaim_pct") if isinstance(path, dict) else None
        if r["confirmed_reversal"]:
            if reclaim is not None and abs(float(reclaim)) >= reversal_threshold_pct:
                reversal_hits += 1
            elif reclaim is None:
                reversal_hits += 1
            if r["confirm_latency_sec"]:
                latencies.append(int(r["confirm_latency_sec"]))

    lines = [_t("similar_events", n=total)]
    if reversal_hits:
        lines.append(_t("reversal_gt", n=reversal_hits, pct=reversal_threshold_pct))
    if latencies:
        med = int(statistics.median(latencies))
        lines.append(_t("median_latency", dur=_fmt_duration(med)))
    elif total >= 3:
        lines.append(_t("median_latency", dur="n/a"))
    return "\n".join(lines)


def build_shock_alert_context(conn: Any, event_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT e.*, p.phase AS pending_phase
        FROM market_events e
        LEFT JOIN market_events_pending_shocks p ON p.event_id = e.id
        WHERE e.id = ?
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return {"event_id": event_id, "found": False}

    raw = json.loads(_col(row, "raw_metrics_json") or "{}")
    window = int(_col(row, "trigger_window_seconds") or 60)
    event_ts = int(row["event_ts"])
    symbol = row["symbol"]
    ret = float(row["return_pct"] or 0)

    return {
        "found": True,
        "event_id": event_id,
        "symbol": symbol,
        "direction": row["direction"],
        "return_pct": ret,
        "window_sec": window,
        "btc_return_pct": _col(row, "btc_return_pct"),
        "eth_return_pct": raw.get("eth_return_pct"),
        "alt_basket_return_pct": _col(row, "market_return_pct"),
        "classification": _classification_label(row["classification"]),
        "phase": _col(row, "pending_phase") or row["phase"],
        "telegram_line": fetch_telegram_line(conn, event_id=event_id, event_ts=event_ts),
        "news_line": fetch_news_line(conn, event_id=event_id, symbol=symbol),
        "history_block": fetch_history_block(
            conn,
            symbol=symbol,
            direction=row["direction"],
            return_pct=ret,
            exclude_event_id=event_id,
        ),
        "volume_zscore": _col(row, "volume_zscore"),
        "relative_return_pct": _col(row, "relative_return_pct"),
    }


def format_structured_shock_alert(ctx: dict[str, Any]) -> str:
    if not ctx.get("found"):
        return f"SHOCK_DETECTED event_id={ctx.get('event_id')} (not found)"

    sym = ctx["symbol"]
    ret = ctx["return_pct"]
    window_txt = _fmt_window(ctx.get("window_sec"))

    lines = [
        _t("shock_header"),
        "",
        "EVENT",
        f"{sym} {ret:+.1f}% {window_txt}",
        "",
        "MARKET",
    ]
    market_lines = [f"BTC {_fmt_return(ctx.get('btc_return_pct'))}"]
    if ctx.get("eth_return_pct") is not None:
        market_lines.append(f"ETH {_fmt_return(ctx['eth_return_pct'])}")
    if ctx.get("alt_basket_return_pct") is not None:
        market_lines.append(f"Alt basket {_fmt_return(ctx['alt_basket_return_pct'])}")
    lines.extend(market_lines)
    lines.extend([
        "",
        "CLASSIFICATION",
        ctx["classification"],
        "",
        "TELEGRAM",
        ctx["telegram_line"],
        "",
        "NEWS",
        ctx["news_line"],
        "",
        "HISTORY",
        ctx["history_block"],
        "",
        f"event_id: {ctx['event_id']}  lifecycle: {ctx['phase']}",
        _t("monitoring"),
    ])
    return "\n".join(lines)


def format_ai_research_note(analysis: Any, ctx: dict[str, Any] | None = None) -> str:
    """Shadow AI note — separate from deterministic shock alert."""
    lines = [
        "AI RESEARCH NOTE — SHADOW ONLY",
        "",
        "AI NOTE",
    ]

    interp = (analysis.movement_interpretation or "UNKNOWN").replace("_", " ").lower()
    bias = analysis.reversal_bias or "NO_VIEW"

    if analysis.short_commentary:
        lines.append(analysis.short_commentary)
    else:
        sym = analysis.symbol
        ret = ctx.get("return_pct") if ctx else None
        head = f"{sym} {ret:+.1f}%" if ret is not None else sym
        lines.append(head)
        lines.append("")
        if analysis.supporting_factors:
            for s in analysis.supporting_factors[:4]:
                lines.append(s)
        lines.append("")
        lines.append(_t("ai_interp", interp=interp))

    if bias == "WAIT_FOR_CONFIRMATION":
        lines.append(_t("ai_wait_r23"))
    elif bias == "FADE_FAVORED":
        lines.append(_t("ai_fade_bias"))
    elif bias == "CONTINUATION_FAVORED":
        lines.append(_t("ai_cont_bias"))
    else:
        lines.append(_t("ai_no_immediate_entry"))

    lines.extend([
        "",
        f"Bias: {bias}  Confidence: {analysis.confidence:.2f}",
        "",
        _t("ai_disclaimer"),
    ])
    return "\n".join(lines)


_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "shock_header": "SHOCK DETECTED — PAPER research only, no live order",
        "monitoring": "Monitoring reversal — no confirmed entry yet.",
        "no_telegram": "No recent Telegram context",
        "news_none": "No news found for {symbol}",
        "news_linked": "News context linked ({source})",
        "history_none": "No similar historical shocks in corpus",
        "similar_events": "{n} similar events",
        "reversal_gt": "{n} gave reversal > {pct}%",
        "median_latency": "median reversal latency: {dur}",
        "ai_interp": "Interpretation: {interp}",
        "ai_wait_r23": "Do not enter immediately. Wait for R2/R3 confirmation.",
        "ai_fade_bias": "Fade bias — wait for reversal confirmation before paper entry.",
        "ai_cont_bias": "Continuation more likely than immediate fade.",
        "ai_no_immediate_entry": "Do not enter immediately.",
        "ai_disclaimer": "This is research commentary, not a live trade signal.",
    },
    "ru": {
        "shock_header": "SHOCK DETECTED — PAPER, не live ордер",
        "monitoring": "Мониторинг reversal — вход не подтверждён.",
        "no_telegram": "Нет недавнего Telegram контекста",
        "news_none": "новостей по {symbol} не найдено",
        "news_linked": "Новость ({source})",
        "history_none": "Похожих событий в корпусе нет",
        "similar_events": "{n} похожих событий",
        "reversal_gt": "{n} дали reversal > {pct}%",
        "median_latency": "median reversal latency: {dur}",
        "ai_interp": "Интерпретация: {interp}",
        "ai_wait_r23": "Не входить немедленно. Ждать R2/R3 confirmation.",
        "ai_fade_bias": "Fade bias — ждать подтверждения reversal.",
        "ai_cont_bias": "Continuation вероятнее немедленного fade.",
        "ai_no_immediate_entry": "Не входить немедленно.",
        "ai_disclaimer": "Research commentary, не live trade signal.",
    },
}
