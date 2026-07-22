"""S49 — Trader UI & Signal-Centric Telegram (AI Analyst terminal)."""

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
from bot.research.ai_analyst.trader_report import (
    format_doctor_telegram_html,
    format_top_news_compact,
    format_trader_report_html,
    load_latest_signal_row,
)

logger = logging.getLogger(__name__)

# Primary trader surface (menu + docs).
TRADER_COMMANDS = frozenset({
    "/report",
    "/signals",
    "/open",
    "/stats",
    "/doctor",
})

# Debug / admin — still routable, hidden from trader menu.
DEBUG_COMMANDS = frozenset({
    "/debug",
    "/market",
    "/btc",
    "/macro",
    "/sp500",
    "/events",
    "/narrative",
    "/context",
    "/health",
    "/closed",
    "/leaderboard",
    "/daily",
})

AI_RESEARCH_COMMANDS = TRADER_COMMANDS | DEBUG_COMMANDS

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


def parse_ai_command_args(text: str) -> tuple[str, list[str]]:
    parts = text.strip().split()
    if not parts:
        return "", []
    cmd = parts[0].split("@")[0].lower()
    return cmd, [p.lower() for p in parts[1:]]


def requires_interactive_handler(cmd: str, args: list[str] | None = None) -> bool:
    args = args or []
    if cmd == "/report":
        return True
    if cmd == "/debug" and (not args or args[0] == "report"):
        return True
    return False


def build_reports_keyboard() -> dict[str, Any]:
    """S49 trader terminal keyboard — signal-centric only."""
    return {
        "inline_keyboard": [
            [
                {"text": "📈 Report", "callback_data": f"{AI_CALLBACK_PREFIX}report"},
                {"text": "📊 Signals", "callback_data": f"{AI_CALLBACK_PREFIX}signals"},
                {"text": "📂 Open", "callback_data": f"{AI_CALLBACK_PREFIX}open"},
            ],
            [
                {"text": "📉 Stats", "callback_data": f"{AI_CALLBACK_PREFIX}stats"},
                {"text": "⚙ Doctor", "callback_data": f"{AI_CALLBACK_PREFIX}doctor"},
            ],
        ],
    }


def format_progress_message(done: dict[str, bool]) -> str:
    lines = ["⏳ <b>Generating trader signal...</b>", ""]
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
    """Verbose intel (debug/admin). Trader path uses compact news formatter."""
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


def build_trader_report_message(*, reports_dir: Path) -> str:
    """S49/S52 default /report body — signal card only."""
    ctx = _load_context_json(reports_dir=reports_dir)
    hist = load_latest_signal_row()
    try:
        from bot.research.ai_analyst.signal_consistency.repository import get_repository
        open_n = get_repository().count_open_trades()
    except Exception:
        open_n = 0
    return format_trader_report_html(ctx, history_row=hist, open_count=open_n)


def build_report_completion_message(*, reports_dir: Path) -> str:
    """Legacy full editorial stack — used by /debug report only."""
    market_md = _read_report_text("market", reports_dir=reports_dir)
    telegram_md = _read_report_text("telegram", reports_dir=reports_dir)
    ctx = _load_context_json(reports_dir=reports_dir)
    ts_html = format_data_timestamp_html(ctx)
    exec_html = format_executive_summary_html(extract_executive_summary(market_md))
    tg_html = markdown_to_telegram_html(telegram_md)
    news_html = format_top_news_compact(
        (ctx.get("intelligence") or {}).get("top_events") or [],
        limit=12,
    )
    parts = [
        section_header("Debug Report", "🛠"),
        "",
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
        news_html,
        "",
        format_separator(),
        "",
        format_narrative_html(ctx),
        "",
        format_separator(),
        "",
        format_context_status_html(ctx),
        "",
        format_separator(),
        "",
        escape("Full analytics retained for AI / learning / ranking — not trader UI."),
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


def _doctor_delivery(*, settings: TelegramTerminalSettings) -> TelegramDelivery:
    from bot.research.market_events.doctor import run_doctor

    text = format_doctor_telegram_html(run_doctor(skip_network=False))
    return TelegramDelivery(
        text=text,
        parse_mode=settings.parse_mode,
        reply_markup=build_reports_keyboard(),
    )


def handle_ai_research_command_sync(
    cmd: str,
    *,
    args: list[str] | None = None,
    settings: TelegramTerminalSettings | None = None,
    reports_dir: Path | None = None,
) -> TelegramDelivery:
    settings = settings or load_telegram_terminal_settings()
    out_dir = reports_dir or REPORTS_DIR
    args = args or []

    if cmd == "/doctor":
        return _doctor_delivery(settings=settings)

    if cmd == "/debug":
        topic = args[0] if args else "report"
        if topic == "help":
            return TelegramDelivery(
                text=escape(
                    "Debug mode: /debug report | /btc /macro /sp500 /events "
                    "/narrative /context /health /daily /leaderboard /closed"
                ),
                parse_mode=settings.parse_mode,
            )
        # Non-interactive debug topics fall through to named handlers below
        if topic in {"btc", "macro", "sp500", "events", "narrative", "context", "health",
                     "market", "closed", "leaderboard", "daily"}:
            cmd = f"/{topic}"
        else:
            raise ValueError("use /debug report for full analytics (interactive)")

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
            reply_markup=build_reports_keyboard() if cmd in {"/signals", "/open", "/stats"} else None,
        )
    raise ValueError(f"sync handler not supported for {cmd}")


def run_interactive_report(
    *,
    cmd: str,
    edit_message: Callable[[str, dict[str, Any] | None], bool],
    settings: TelegramTerminalSettings | None = None,
    reports_dir: Path | None = None,
    args: list[str] | None = None,
) -> TelegramDelivery:
    """Run /report (trader card) or /debug report (full analytics) with progress edits."""
    settings = settings or load_telegram_terminal_settings()
    out_dir = reports_dir or REPORTS_DIR
    args = args or []
    done: dict[str, bool] = {s: False for s in _PROGRESS_STEPS}
    debug_mode = cmd == "/debug" or (cmd == "/report" and "debug" in args)

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

        # Always refresh live data + full regeneration (artifacts kept for AI).
        result = _regenerate_full(
            reports_dir=out_dir,
            on_progress=on_progress,
            refresh_live_data=True,
        )
        if not result.get("ok", True):
            raise RuntimeError(str(result.get("error") or "generation failed"))

        if debug_mode:
            final = build_report_completion_message(reports_dir=out_dir)
        else:
            final = build_trader_report_message(reports_dir=out_dir)
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
    """Inline keyboard — trader actions (cached where possible)."""
    settings = load_telegram_terminal_settings()
    out_dir = reports_dir or REPORTS_DIR
    action = action.lower().strip()

    if action == "report":
        # Fast path: rebuild trader card from latest context (no LLM).
        text = build_trader_report_message(reports_dir=out_dir)
        return TelegramDelivery(
            text=text,
            parse_mode=settings.parse_mode,
            reply_markup=build_reports_keyboard(),
        )
    if action == "signals":
        return handle_ai_research_command_sync("/signals", settings=settings, reports_dir=out_dir)
    if action == "open":
        return handle_ai_research_command_sync("/open", settings=settings, reports_dir=out_dir)
    if action == "stats":
        return handle_ai_research_command_sync("/stats", settings=settings, reports_dir=out_dir)
    if action == "doctor":
        # Skip live HTTP on button tap for snappy UX
        from bot.research.market_events.doctor import run_doctor
        return TelegramDelivery(
            text=format_doctor_telegram_html(run_doctor(skip_network=True)),
            parse_mode=settings.parse_mode,
            reply_markup=build_reports_keyboard(),
        )

    # Legacy debug callbacks still work if somehow invoked
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
