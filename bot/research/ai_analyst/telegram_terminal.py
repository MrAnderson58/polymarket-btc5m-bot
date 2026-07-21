"""S46.4 — Telegram Research Terminal (AI Analyst primary interface)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bot.research.ai_analyst.config import (
    REPORTS_DIR,
    TelegramTerminalSettings,
    load_telegram_terminal_settings,
)
from bot.research.ai_analyst.context_builder import build_market_context
from bot.research.ai_analyst.reasoning_engine import enrich_context_for_analysis
from bot.research.ai_analyst.report_cache import (
    ensure_fresh_report,
    ensure_full_report_suite,
    file_age_minutes,
    is_report_fresh,
    report_path,
)
from bot.research.ai_analyst.report_generator import run_ai_analyst
from bot.research.ai_analyst.telegram_formatter import (
    escape,
    extract_executive_summary,
    format_context_status_html,
    format_data_timestamp_html,
    format_error_html,
    format_executive_summary_html,
    format_report_body_html,
    format_separator,
    markdown_to_telegram_html,
    section_header,
    truncate_telegram,
)

logger = logging.getLogger(__name__)

AI_RESEARCH_COMMANDS = frozenset({
    "/report",
    "/market",
    "/btc",
    "/macro",
    "/sp500",
    "/events",
    "/narrative",
    "/context",
    "/health",
    "/signals",
    "/open",
    "/closed",
    "/stats",
    "/leaderboard",
    "/daily",
})

AI_CALLBACK_PREFIX = "ai:"

_PROGRESS_STEPS = (
    "Context",
    "Intelligence",
    "Macro",
    "ETF",
    "AI Analysis",
    "Telegram Report",
)

_REPORT_FLAGS: dict[str, list[str] | None] = {
    "market": None,
    "btc": ["btc"],
    "macro": ["macro"],
    "sp500": ["sp500"],
}

_HEALTH_SERVICES: tuple[tuple[str, str], ...] = (
    ("RSS", "news-intel"),
    ("Telegram", "telegram"),
    ("Twitter", "multi-source"),
    ("Macro", "g3-live"),
    ("Polymarket", "event-engine"),
    ("Narrative Engine", "narrative-engine"),
)


@dataclass(frozen=True)
class TelegramDelivery:
    text: str
    parse_mode: str = "HTML"
    reply_markup: dict[str, Any] | None = None
    already_delivered: bool = False


def is_ai_research_command(text: str | None) -> bool:
    if not text or not text.strip().startswith("/"):
        return False
    cmd = text.strip().split()[0].split("@")[0].lower()
    return cmd in AI_RESEARCH_COMMANDS


def normalize_ai_command(text: str) -> str:
    return text.strip().split()[0].split("@")[0].lower()


def requires_interactive_handler(cmd: str) -> bool:
    return cmd in {"/report", "/market"}


def build_reports_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Market", "callback_data": f"{AI_CALLBACK_PREFIX}market"},
                {"text": "BTC", "callback_data": f"{AI_CALLBACK_PREFIX}btc"},
                {"text": "Macro", "callback_data": f"{AI_CALLBACK_PREFIX}macro"},
                {"text": "SP500", "callback_data": f"{AI_CALLBACK_PREFIX}sp500"},
            ],
            [
                {"text": "Intelligence", "callback_data": f"{AI_CALLBACK_PREFIX}events"},
                {"text": "Narratives", "callback_data": f"{AI_CALLBACK_PREFIX}narrative"},
                {"text": "Health", "callback_data": f"{AI_CALLBACK_PREFIX}health"},
            ],
        ],
    }


def format_progress_message(done: dict[str, bool]) -> str:
    lines = ["⏳ <b>Generating AI Market Report...</b>", ""]
    for step in _PROGRESS_STEPS:
        mark = "✓" if done.get(step) else "…"
        lines.append(f"{mark} {escape(step)}")
    return "\n".join(lines)


def _load_context_json(*, reports_dir: Path) -> dict[str, Any]:
    path = report_path("context", reports_dir=reports_dir)
    if not path.is_file():
        ctx = enrich_context_for_analysis(build_market_context(live_enrich=True))
        return ctx
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("context json read failed: %s", exc)
        return enrich_context_for_analysis(build_market_context(live_enrich=True))


def _read_report_text(key: str, *, reports_dir: Path) -> str:
    path = report_path(key, reports_dir=reports_dir)
    if not path.is_file():
        return f"Report not found: {path.name}. Run /report first."
    return path.read_text(encoding="utf-8")


def _regenerate_full(
    *,
    reports_dir: Path,
    on_progress: Callable[[dict[str, bool]], None] | None,
    refresh_live_data: bool = False,
) -> dict[str, Any]:
    return run_ai_analyst(
        flags=None,
        reports_dir=reports_dir,
        on_progress=on_progress,
        refresh_live_data=refresh_live_data,
    )


def _regenerate_partial(
    flags: list[str],
    *,
    reports_dir: Path,
    on_progress: Callable[[dict[str, bool]], None] | None,
    refresh_live_data: bool = False,
) -> dict[str, Any]:
    return run_ai_analyst(
        flags=flags,
        reports_dir=reports_dir,
        on_progress=on_progress,
        refresh_live_data=refresh_live_data,
    )


def format_events_html(ctx: dict[str, Any], *, limit: int = 10) -> str:
    events = (ctx.get("intelligence") or {}).get("top_events") or []
    lines = [section_header("Intelligence Events", "📰"), ""]
    if not events:
        lines.append(escape("No intel events in lookback."))
        return truncate_telegram("\n".join(lines))

    for ev in events[:limit]:
        score = ev.get("net_score")
        if score is None:
            score = ev.get("sentiment")
        title = str(ev.get("title") or "—")
        why = str(ev.get("why_it_matters") or ev.get("summary") or "—")
        symbols = ev.get("symbols") or ev.get("symbols_json")
        if isinstance(symbols, str):
            try:
                symbols = json.loads(symbols)
            except Exception:
                pass
        assets = ", ".join(str(s) for s in symbols) if isinstance(symbols, list) else str(symbols or "—")
        lines.extend([
            f"<b>Score:</b> {escape(str(score))}",
            f"<b>Title:</b> {escape(title)}",
            f"<b>Why it matters:</b> {escape(why[:280])}",
            f"<b>Affected assets:</b> {escape(assets)}",
            format_separator(),
            "",
        ])
    return truncate_telegram("\n".join(lines).strip())


def format_narrative_html(ctx: dict[str, Any]) -> str:
    hints = ctx.get("reasoning_hints") or {}
    narratives = hints.get("narrative_candidates") or []
    lines = [section_header("Current Narrative", "📖"), ""]
    if not narratives:
        lines.append(escape("No narrative themes in latest context."))
        return truncate_telegram("\n".join(lines))

    for row in narratives[:8]:
        lines.extend([
            f"<b>{escape(str(row.get('narrative') or '—'))}</b>",
            f"Strength: {escape(str(row.get('strength') or '—'))}",
            f"Evidence: {escape(str(row.get('evidence') or '—'))}",
            "",
        ])
    return truncate_telegram("\n".join(lines).strip())


def format_research_health_html(*, reports_dir: Path) -> str:
    from bot.ops.process_utils import find_service_processes
    from bot.research.market_events.process_manager import _service_by_key

    lines = [section_header("Research Health", "🏥"), ""]

    for label, key in _HEALTH_SERVICES:
        try:
            svc = _service_by_key(key)
            procs = find_service_processes(svc)
            status = f"✓ running PID {procs[0].pid}" if procs else "✗ down"
        except Exception as exc:
            status = f"✗ error ({exc})"
        lines.append(f"<b>{escape(label)}:</b> {escape(status)}")

    # AI Analyst — report freshness
    mpath = report_path("market", reports_dir=reports_dir)
    age = file_age_minutes(mpath)
    if age is None:
        ai_status = "no report yet"
    elif age <= load_telegram_terminal_settings().report_cache_minutes:
        ai_status = f"fresh ({age:.1f}m ago)"
    else:
        ai_status = f"stale ({age:.1f}m ago)"
    lines.append(f"<b>AI Analyst:</b> {escape(ai_status)}")

    ctx_path = report_path("context", reports_dir=reports_dir)
    last = "—"
    if ctx_path.is_file():
        last = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ctx_path.stat().st_mtime))
    lines.extend(["", f"<b>Last Update:</b> {escape(last)}"])
    return truncate_telegram("\n".join(lines))


def build_report_completion_message(*, reports_dir: Path) -> str:
    market_md = _read_report_text("market", reports_dir=reports_dir)
    telegram_md = _read_report_text("telegram", reports_dir=reports_dir)
    ctx_path = report_path("context", reports_dir=reports_dir)
    ctx: dict[str, Any] = {}
    if ctx_path.is_file():
        try:
            ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
        except Exception:
            ctx = {}
    ts_html = format_data_timestamp_html(ctx)
    exec_html = format_executive_summary_html(extract_executive_summary(market_md))
    tg_html = markdown_to_telegram_html(telegram_md)
    parts = [
        ts_html,
        "",
        format_separator(),
        "",
        exec_html,
        "",
        format_separator(),
        "",
        section_header("Telegram Edition", "📱"),
        "",
        tg_html,
        "",
        format_separator(),
        "",
        section_header("Reports", "📊"),
        escape("Use buttons below — no re-generation."),
    ]
    return truncate_telegram("\n".join(parts))


def deliver_report_key(
    key: str,
    *,
    settings: TelegramTerminalSettings | None = None,
    reports_dir: Path | None = None,
    regenerate_if_stale: bool = True,
    on_progress: Callable[[dict[str, bool]], None] | None = None,
) -> TelegramDelivery:
    settings = settings or load_telegram_terminal_settings()
    out_dir = reports_dir or REPORTS_DIR
    titles = {
        "market": ("Market Report", "📊"),
        "btc": ("BTC Brief", "₿"),
        "macro": ("Macro Brief", "🏛"),
        "sp500": ("S&P500 Brief", "📈"),
    }
    if key not in titles:
        raise KeyError(key)

    def _regen() -> dict[str, Any]:
        if key == "market":
            return _regenerate_full(
                reports_dir=out_dir, on_progress=on_progress, refresh_live_data=True,
            )
        flags = _REPORT_FLAGS.get(key)
        if flags:
            return _regenerate_partial(
                flags, reports_dir=out_dir, on_progress=on_progress, refresh_live_data=True,
            )
        return _regenerate_full(
            reports_dir=out_dir, on_progress=on_progress, refresh_live_data=True,
        )

    if regenerate_if_stale:
        ensure_fresh_report(
            key,
            ttl_minutes=settings.report_cache_minutes,
            reports_dir=out_dir,
            regenerate=_regen,
        )
    text = format_report_body_html(
        _read_report_text(key, reports_dir=out_dir),
        title=titles[key][0],
        emoji=titles[key][1],
    )
    return TelegramDelivery(text=text, parse_mode=settings.parse_mode)


def handle_ai_research_command_sync(
    cmd: str,
    *,
    settings: TelegramTerminalSettings | None = None,
    reports_dir: Path | None = None,
) -> TelegramDelivery:
    settings = settings or load_telegram_terminal_settings()
    out_dir = reports_dir or REPORTS_DIR

    if cmd == "/btc":
        return deliver_report_key("btc", settings=settings, reports_dir=out_dir)
    if cmd == "/macro":
        return deliver_report_key("macro", settings=settings, reports_dir=out_dir)
    if cmd == "/sp500":
        return deliver_report_key("sp500", settings=settings, reports_dir=out_dir)
    if cmd == "/events":
        ctx = _load_context_json(reports_dir=out_dir)
        return TelegramDelivery(
            text=format_events_html(ctx),
            parse_mode=settings.parse_mode,
        )
    if cmd == "/narrative":
        ctx = _load_context_json(reports_dir=out_dir)
        return TelegramDelivery(
            text=format_narrative_html(ctx),
            parse_mode=settings.parse_mode,
        )
    if cmd == "/context":
        ctx = _load_context_json(reports_dir=out_dir)
        return TelegramDelivery(
            text=format_context_status_html(ctx),
            parse_mode=settings.parse_mode,
        )
    if cmd == "/health":
        return TelegramDelivery(
            text=format_research_health_html(reports_dir=out_dir),
            parse_mode=settings.parse_mode,
        )
    from bot.research.ai_analyst.strategy_validation.telegram_views import (
        S48_COMMANDS,
        handle_s48_command,
    )
    if cmd in S48_COMMANDS:
        return TelegramDelivery(
            text=handle_s48_command(cmd),
            parse_mode=settings.parse_mode,
        )
    raise ValueError(f"sync handler not supported for {cmd}")


def run_interactive_report(
    *,
    cmd: str,
    edit_message: Callable[[str, dict[str, Any] | None], bool],
    settings: TelegramTerminalSettings | None = None,
    reports_dir: Path | None = None,
) -> TelegramDelivery:
    """Run /report or stale /market with progress edits on one message."""
    settings = settings or load_telegram_terminal_settings()
    out_dir = reports_dir or REPORTS_DIR
    done: dict[str, bool] = {s: False for s in _PROGRESS_STEPS}

    def on_progress(state: dict[str, bool]) -> None:
        done.update(state)
        if settings.enable_progress_messages:
            edit_message(format_progress_message(done), None)

    if settings.enable_progress_messages:
        edit_message(format_progress_message(done), None)

    try:
        if cmd == "/market":
            stale = not is_report_fresh(
                "market",
                ttl_minutes=settings.report_cache_minutes,
                reports_dir=out_dir,
            )
            if stale:
                ensure_full_report_suite(
                    settings=settings,
                    reports_dir=out_dir,
                    regenerate=lambda: _regenerate_full(
                        reports_dir=out_dir,
                        on_progress=on_progress,
                        refresh_live_data=True,
                    ),
                )
            text = format_report_body_html(
                _read_report_text("market", reports_dir=out_dir),
                title="Market Report",
                emoji="📊",
            )
            return TelegramDelivery(
                text=text,
                parse_mode=settings.parse_mode,
                reply_markup=build_reports_keyboard(),
                already_delivered=True,
            )

        # /report — always refresh live data + full regeneration (no report cache)
        result = _regenerate_full(
            reports_dir=out_dir,
            on_progress=on_progress,
            refresh_live_data=True,
        )
        if not result.get("ok", True):
            raise RuntimeError(str(result.get("error") or "generation failed"))

        final = build_report_completion_message(reports_dir=out_dir)
        markup = build_reports_keyboard()
        edit_message(final, markup)
        return TelegramDelivery(
            text=final,
            parse_mode=settings.parse_mode,
            reply_markup=markup,
            already_delivered=True,
        )
    except Exception as exc:
        logger.exception("AI research report failed: %s", exc)
        err = format_error_html(str(exc))
        edit_message(err, None)
        return TelegramDelivery(text=err, parse_mode=settings.parse_mode, already_delivered=True)


def handle_ai_callback(action: str, *, reports_dir: Path | None = None) -> TelegramDelivery:
    """Inline keyboard — serve cached artifacts only (no regeneration)."""
    settings = load_telegram_terminal_settings()
    out_dir = reports_dir or REPORTS_DIR
    action = action.lower().strip()

    if action == "market":
        return deliver_report_key(
            "market", settings=settings, reports_dir=out_dir, regenerate_if_stale=False,
        )
    if action == "btc":
        return deliver_report_key(
            "btc", settings=settings, reports_dir=out_dir, regenerate_if_stale=False,
        )
    if action == "macro":
        return deliver_report_key(
            "macro", settings=settings, reports_dir=out_dir, regenerate_if_stale=False,
        )
    if action == "sp500":
        return deliver_report_key(
            "sp500", settings=settings, reports_dir=out_dir, regenerate_if_stale=False,
        )
    if action == "events":
        ctx = _load_context_json(reports_dir=out_dir)
        return TelegramDelivery(text=format_events_html(ctx), parse_mode=settings.parse_mode)
    if action == "narrative":
        ctx = _load_context_json(reports_dir=out_dir)
        return TelegramDelivery(text=format_narrative_html(ctx), parse_mode=settings.parse_mode)
    if action == "health":
        return TelegramDelivery(
            text=format_research_health_html(reports_dir=out_dir),
            parse_mode=settings.parse_mode,
        )
    return TelegramDelivery(text=escape(f"Unknown action: {action}"), parse_mode=settings.parse_mode)


def parse_ai_callback(data: str | None) -> str | None:
    if not data or not data.startswith(AI_CALLBACK_PREFIX):
        return None
    return data[len(AI_CALLBACK_PREFIX):]
