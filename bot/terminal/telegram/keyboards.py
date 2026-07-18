"""Inline keyboards for AI Trading Terminal — E2E flow buttons (V7.1.0)."""

from __future__ import annotations

from typing import Any, Sequence

CALLBACK_PREFIX = "term:"
FAV_PREFIX = "fav:"
DECISION_PREFIX = "decision:"
WATCH_ADD_PREFIX = "watchadd:"
WHY_PREFIX = "why:"
RESEARCH_PREFIX = "research:"
TIMELINE_PREFIX = "timeline:"

MAIN_MENU_BUTTONS: list[list[dict[str, str]]] = [
    [
        {"text": "📈 Markets", "callback_data": "term:markets"},
        {"text": "🎯 Signals", "callback_data": "term:signals"},
        {"text": "💼 Portfolio", "callback_data": "term:portfolio"},
    ],
    [
        {"text": "📊 Positions", "callback_data": "term:positions"},
        {"text": "💰 Account", "callback_data": "term:account"},
        {"text": "⭐ Watch", "callback_data": "term:watch"},
    ],
    [
        {"text": "🔔 Alerts", "callback_data": "term:alerts"},
        {"text": "☀️ Brief", "callback_data": "term:brief"},
        {"text": "⚙ Settings", "callback_data": "term:settings"},
    ],
]

NAV_ROW: list[dict[str, str]] = [
    {"text": "⬅ Back", "callback_data": "term:back"},
    {"text": "🏠 Home", "callback_data": "term:home"},
]


def main_menu_keyboard() -> dict[str, Any]:
    return {"inline_keyboard": [row[:] for row in MAIN_MENU_BUTTONS]}


def home_keyboard() -> dict[str, Any]:
    return main_menu_keyboard()


def screen_keyboard() -> dict[str, Any]:
    rows = [row[:] for row in MAIN_MENU_BUTTONS]
    rows.append(NAV_ROW[:])
    return {"inline_keyboard": rows}


def nav_keyboard() -> dict[str, Any]:
    return {"inline_keyboard": [NAV_ROW[:]]}


def flow_keyboard(*, symbol: str | None = None) -> dict[str, Any]:
    """E2E chain shortcuts present on most screens."""
    rows: list[list[dict[str, str]]] = [row[:] for row in MAIN_MENU_BUTTONS]
    if symbol:
        sym = symbol.upper()
        rows.append(
            [
                {"text": f"🧠 Decision {sym}", "callback_data": f"term:{DECISION_PREFIX}{sym}"},
                {"text": f"❓ Why {sym}", "callback_data": f"term:{WHY_PREFIX}{sym}"},
            ]
        )
        rows.append(
            [
                {"text": "🔬 Research", "callback_data": f"term:{RESEARCH_PREFIX}{sym}"},
                {"text": "🕒 Timeline", "callback_data": f"term:{TIMELINE_PREFIX}{sym}"},
            ]
        )
    else:
        rows.append(
            [
                {"text": "🧠 Decision", "callback_data": "term:decision"},
                {"text": "🔬 Research", "callback_data": "term:research"},
            ]
        )
    rows.append(NAV_ROW[:])
    return {"inline_keyboard": rows}


def favorite_button(symbol: str, *, favorited: bool = False) -> dict[str, str]:
    sym = (symbol or "").strip().upper()
    label = f"★ {sym}" if favorited else f"⭐ Favorite · {sym}"
    return {"text": label, "callback_data": f"{CALLBACK_PREFIX}{FAV_PREFIX}{sym}"}


def signals_keyboard(
    symbols: Sequence[str],
    *,
    favorited: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    return signals_flow_keyboard(symbols, favorited=favorited)


def signals_flow_keyboard(
    symbols: Sequence[str],
    *,
    favorited: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Signals: Decision + Favorite per symbol."""
    favs = {s.upper() for s in (favorited or set())}
    rows: list[list[dict[str, str]]] = []
    for raw in symbols:
        sym = (raw or "").strip().upper()
        if not sym or sym == "—":
            continue
        rows.append(
            [
                {"text": f"🧠 {sym}", "callback_data": f"term:{DECISION_PREFIX}{sym}"},
                favorite_button(sym, favorited=sym in favs),
            ]
        )
    rows.append(
        [
            {"text": "⭐ Watchlist", "callback_data": "term:watch"},
            {"text": "☀️ Brief", "callback_data": "term:brief"},
        ]
    )
    rows.append(NAV_ROW[:])
    return {"inline_keyboard": rows}


def decision_keyboard(symbol: str) -> dict[str, Any]:
    sym = (symbol or "BTC").upper()
    return {
        "inline_keyboard": [
            [
                {
                    "text": "⭐ Add to Watchlist",
                    "callback_data": f"term:{WATCH_ADD_PREFIX}{sym}",
                },
                {"text": f"❓ Why {sym}", "callback_data": f"term:{WHY_PREFIX}{sym}"},
            ],
            [
                {"text": "💼 Portfolio", "callback_data": "term:portfolio"},
                {"text": "☀️ Morning Brief", "callback_data": "term:brief"},
            ],
            [
                {"text": "🔬 Research Review", "callback_data": f"term:{RESEARCH_PREFIX}{sym}"},
                {"text": "🕒 Timeline", "callback_data": f"term:{TIMELINE_PREFIX}{sym}"},
            ],
            [
                {"text": "🎯 Signals", "callback_data": "term:signals"},
                {"text": "🏠 Home", "callback_data": "term:home"},
            ],
        ]
    }


def watchlist_keyboard(symbols: Sequence[str]) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for raw in symbols:
        sym = (raw or "").strip().upper()
        if not sym:
            continue
        row.append({"text": f"★ {sym}", "callback_data": f"{CALLBACK_PREFIX}{FAV_PREFIX}{sym}"})
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if symbols:
        first = str(symbols[0]).upper()
        rows.append(
            [
                {"text": f"🧠 Decision {first}", "callback_data": f"term:{DECISION_PREFIX}{first}"},
                {"text": "☀️ Brief", "callback_data": "term:brief"},
            ]
        )
    rows.append(NAV_ROW[:])
    return {"inline_keyboard": rows}


def is_terminal_callback(data: str | None) -> bool:
    return bool(data) and str(data).startswith(CALLBACK_PREFIX)


def parse_terminal_callback(data: str) -> str:
    raw = str(data)
    if raw.startswith(CALLBACK_PREFIX):
        return raw[len(CALLBACK_PREFIX) :].strip().lower() or "home"
    return "home"


def parse_favorite_symbol(data: str) -> str | None:
    raw = str(data)
    if not raw.startswith(CALLBACK_PREFIX):
        return None
    rest = raw[len(CALLBACK_PREFIX) :]
    if not rest.lower().startswith("fav:"):
        return None
    sym = rest.split(":", 1)[-1].strip().upper()
    return sym or None


def parse_callback_intent(data: str) -> tuple[str, str | None]:
    """
    Map term:* callback → (screen, symbol|None).

    Examples:
      term:signals → (signals, None)
      term:decision:BTC → (decision, BTC)
      term:watchadd:BTC → (watch_add, BTC)
      term:fav:ETH → (fav, ETH)
    """
    raw = str(data)
    if not raw.startswith(CALLBACK_PREFIX):
        return "home", None
    rest = raw[len(CALLBACK_PREFIX) :]
    low = rest.lower()

    if low.startswith("fav:"):
        return "fav", rest.split(":", 1)[-1].strip().upper() or None
    if low.startswith(DECISION_PREFIX):
        return "decision", rest.split(":", 1)[-1].strip().upper() or None
    if low.startswith(WATCH_ADD_PREFIX):
        return "watch_add", rest.split(":", 1)[-1].strip().upper() or None
    if low.startswith(WHY_PREFIX):
        return "why", rest.split(":", 1)[-1].strip().upper() or None
    if low.startswith(RESEARCH_PREFIX):
        return "research", rest.split(":", 1)[-1].strip().upper() or None
    if low.startswith(TIMELINE_PREFIX):
        return "timeline", rest.split(":", 1)[-1].strip().upper() or None
    if ":" in rest:
        # unknown prefixed — treat left as screen
        screen, _, sym = rest.partition(":")
        return screen.strip().lower() or "home", sym.strip().upper() or None
    return rest.strip().lower() or "home", None


__all__ = [
    "CALLBACK_PREFIX",
    "DECISION_PREFIX",
    "FAV_PREFIX",
    "MAIN_MENU_BUTTONS",
    "NAV_ROW",
    "RESEARCH_PREFIX",
    "TIMELINE_PREFIX",
    "WATCH_ADD_PREFIX",
    "WHY_PREFIX",
    "decision_keyboard",
    "favorite_button",
    "flow_keyboard",
    "home_keyboard",
    "is_terminal_callback",
    "main_menu_keyboard",
    "nav_keyboard",
    "parse_callback_intent",
    "parse_favorite_symbol",
    "parse_terminal_callback",
    "screen_keyboard",
    "signals_flow_keyboard",
    "signals_keyboard",
    "watchlist_keyboard",
]
