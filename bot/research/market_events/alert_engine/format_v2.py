"""Phase E.5 — localized unified Telegram alert formatting."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.alert_config import alert_locale
from bot.research.market_events.alert_format import (
    build_shock_alert_context,
    fetch_history_block,
    fetch_news_line,
    fetch_telegram_line,
    _fmt_duration,
    _fmt_window,
)


def _t(key: str, **kwargs: Any) -> str:
    loc = alert_locale()
    table = _STRINGS.get(loc, _STRINGS["en"])
    text = table.get(key, _STRINGS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def _col(row: Any, name: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return default


def _history_success_block(conn: Any, *, symbol: str, direction: str, ret: float, event_id: int) -> str:
    block = fetch_history_block(
        conn, symbol=symbol, direction=direction, return_pct=ret, exclude_event_id=event_id,
    )
    rows = conn.execute(
        """
        SELECT p.confirmed_reversal FROM market_events e
        LEFT JOIN market_events_pending_shocks p ON p.event_id = e.id
        WHERE e.symbol = ? AND e.id != ? AND ABS(e.return_pct) >= ?
        ORDER BY e.event_ts DESC LIMIT 200
        """,
        (symbol, event_id, max(1.0, abs(ret) * 0.65)),
    ).fetchall()
    if not rows:
        return block
    total = len(rows)
    revs = sum(1 for r in rows if r["confirmed_reversal"])
    rate = int(100 * revs / total) if total else 0
    lines = block.split("\n")
    if revs:
        lines.append(_t("success_rate", rate=rate))
    return "\n".join(lines)


def _telegram_context_block(conn: Any, *, event_id: int, event_ts: int) -> str:
    rows = conn.execute(
        """
        SELECT source, context_type FROM market_event_context
        WHERE event_id = ? AND context_type IN (
          'TELEGRAM_SIGNAL', 'TRADER_THESIS', 'MARKET_COMMENTARY'
        )
        """,
        (event_id,),
    ).fetchall()
    if not rows:
        return _t("telegram_none")
    sources = sorted({r["source"] for r in rows})
    src_label = sources[0] if len(sources) == 1 else ", ".join(sources[:2])
    return _t("telegram_posts", n=len(rows), source=src_label)


def _ai_summary_line(conn: Any, *, event_id: int) -> str:
    row = conn.execute(
        """
        SELECT structured_output_json, reversal_bias, movement_interpretation
        FROM market_event_ai_analyses WHERE event_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return _t("ai_pending")
    try:
        data = json.loads(row["structured_output_json"] or "{}")
        comment = (data.get("short_commentary") or "").strip()
        if comment:
            lines = comment.split("\n")[:2]
            bias = row["reversal_bias"]
            if bias == "WAIT_FOR_CONFIRMATION":
                lines.append(_t("ai_wait_r2"))
            return "\n".join(lines)
    except Exception:
        pass
    interp = (row["movement_interpretation"] or "UNKNOWN").replace("_", " ").lower()
    return _t("ai_interp_short", interp=interp)


def format_shock_alert_v2(conn: Any, event_id: int) -> str:
    ctx = build_shock_alert_context(conn, event_id)
    if not ctx.get("found"):
        return f"🚨 SHOCK DETECTED event_id={event_id}"

    sym = ctx["symbol"]
    ret = ctx["return_pct"]
    window_m = max(1, round(int(ctx.get("window_sec") or 60) / 60))

    history = _history_success_block(
        conn, symbol=sym, direction=ctx["direction"], ret=ret, event_id=event_id,
    )
    classification = (ctx.get("classification") or "unknown").upper().replace(" ", "_")
    if "MARKET" in classification:
        classification = "MARKET_WIDE"
    elif "ASSET" in classification:
        classification = "ASSET_SPECIFIC"

    lines = [
        _t("shock_title"),
        "",
        _t("label_symbol"),
        f"{sym}USDT" if not sym.endswith("USDT") else sym,
        "",
        _t("label_move"),
        f"{ret:+.2f}%",
        "",
        _t("label_window"),
        f"{window_m}m",
        "",
        _t("label_classification"),
        classification,
        "",
        _t("label_history"),
        history,
        "",
        _t("label_telegram"),
        _telegram_context_block(conn, event_id=event_id, event_ts=0),
        "",
        _t("label_news"),
        ctx.get("news_line") or _t("news_none_short"),
        "",
        _t("label_ai_summary"),
        _ai_summary_line(conn, event_id=event_id),
        "",
        _t("label_lifecycle"),
        (ctx.get("phase") or "MONITORING_REVERSAL").upper(),
        "",
        _t("paper_only"),
    ]
    return "\n".join(lines)


def format_reversal_alert_v2(
    conn: Any,
    *,
    event_id: int,
    reversal_variant: str,
    confirm_latency_sec: int | None,
    extreme_price: float | None,
    path_json: dict | None,
    paper_runs: int,
) -> str:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    sym = row["symbol"] if row else "?"
    run = conn.execute(
        """
        SELECT entry_price, initial_stop, exit_variant
        FROM paper_strategy_runs
        WHERE event_id = ? AND reversal_variant = ? AND entry_price IS NOT NULL
        LIMIT 1
        """,
        (event_id, reversal_variant),
    ).fetchone()

    entry = _col(run, "entry_price")
    stop = _col(run, "initial_stop")
    target = None
    if entry and run and row:
        from bot.research.market_events.config import EXIT_POLICIES
        ev = run["exit_variant"]
        pol = EXIT_POLICIES.get(ev, {})
        tp_pct = pol.get("tp_pct") or pol.get("partial_tp_pct")
        if tp_pct:
            pct = float(tp_pct)
            fade_dir = "DOWN" if row["direction"] == "UP" else "UP"
            if fade_dir == "UP":
                target = entry * (1 + pct / 100)
            else:
                target = entry * (1 - pct / 100)

    latency = _fmt_duration(confirm_latency_sec) if confirm_latency_sec else "?"

    lines = [
        _t("reversal_title"),
        "",
        sym,
        "",
        _t("label_strategy"),
        reversal_variant,
        "",
        _t("label_latency"),
        latency,
    ]
    if entry:
        lines.extend(["", _t("label_paper_entry"), f"{entry:g}"])
    if stop:
        lines.extend(["", _t("label_stop"), f"{stop:g}"])
    if target:
        lines.extend(["", _t("label_target"), f"{target:g}"])
    lines.extend(["", _t("paper_only")])
    return "\n".join(lines)


def format_paper_result_v2(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    reversal_variant: str,
    exit_variant: str,
    update_type: str,
    detail: str = "",
) -> str:
    run = conn.execute(
        """
        SELECT gross_return, net_return, duration_seconds, exit_reason, entry_price, exit_price
        FROM paper_strategy_runs
        WHERE event_id = ? AND reversal_variant = ? AND exit_variant = ?
        """,
        (event_id, reversal_variant, exit_variant),
    ).fetchone()

    pnl = _col(run, "net_return") or _col(run, "gross_return")
    dur = _col(run, "duration_seconds")
    holding = _fmt_duration(dur) if dur else "?"
    reason = exit_variant or _col(run, "exit_reason") or update_type
    result = update_type
    if update_type == "TP":
        result = "TP1"
    elif update_type == "STOP":
        result = "STOP"

    lines = [
        _t("paper_result_title"),
        "",
        symbol,
        "",
        _t("label_result"),
        result,
        "",
        _t("label_pnl"),
        f"{pnl:+.2f}%" if pnl is not None else detail or "?",
        "",
        _t("label_holding"),
        holding,
        "",
        _t("label_reason"),
        reason,
        "",
        _t("paper_only"),
    ]
    return "\n".join(lines)


_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "shock_title": "🚨 SHOCK DETECTED",
        "reversal_title": "✅ REVERSAL CONFIRMED",
        "paper_result_title": "🏁 PAPER RESULT",
        "paper_only": "PAPER ONLY",
        "label_symbol": "Symbol:",
        "label_move": "Move:",
        "label_window": "Window:",
        "label_classification": "Classification:",
        "label_history": "History:",
        "label_telegram": "Telegram context:",
        "label_news": "News:",
        "label_ai_summary": "AI Summary:",
        "label_lifecycle": "Lifecycle:",
        "label_strategy": "Strategy:",
        "label_latency": "Latency:",
        "label_paper_entry": "Paper entry:",
        "label_stop": "Stop:",
        "label_target": "Target:",
        "label_result": "Result:",
        "label_pnl": "PnL:",
        "label_holding": "Holding:",
        "label_reason": "Reason:",
        "success_rate": "{rate}% success",
        "telegram_none": "No related posts",
        "telegram_posts": "{n} related posts\n({source})",
        "news_none_short": "No major news",
        "ai_pending": "Analysis pending",
        "ai_wait_r2": "Wait for R2 confirmation.",
        "ai_interp_short": "Looks like {interp}.",
    },
    "ru": {
        "shock_title": "🚨 SHOCK DETECTED",
        "reversal_title": "✅ REVERSAL CONFIRMED",
        "paper_result_title": "🏁 PAPER RESULT",
        "paper_only": "PAPER ONLY",
        "label_symbol": "Symbol:",
        "label_move": "Move:",
        "label_window": "Window:",
        "label_classification": "Classification:",
        "label_history": "History:",
        "label_telegram": "Telegram context:",
        "label_news": "News:",
        "label_ai_summary": "AI Summary:",
        "label_lifecycle": "Lifecycle:",
        "label_strategy": "Strategy:",
        "label_latency": "Latency:",
        "label_paper_entry": "Paper entry:",
        "label_stop": "Stop:",
        "label_target": "Target:",
        "label_result": "Result:",
        "label_pnl": "PnL:",
        "label_holding": "Holding:",
        "label_reason": "Reason:",
        "success_rate": "{rate}% success",
        "telegram_none": "Нет связанных постов",
        "telegram_posts": "{n} связанных постов\n({source})",
        "news_none_short": "Нет важных новостей",
        "ai_pending": "Анализ в очереди",
        "ai_wait_r2": "Ждать R2 confirmation.",
        "ai_interp_short": "Похоже на {interp}.",
    },
}
