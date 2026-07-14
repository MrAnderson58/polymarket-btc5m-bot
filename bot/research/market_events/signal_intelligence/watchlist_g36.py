"""Phase G.3.6 — Telegram watch list."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.db import insert_returning_id

_TABLE = "market_watchlist_g36"


def add_watchlist_symbol_g36(conn: Any, *, symbol: str, chat_id: int | None = None) -> bool:
    sym = symbol.upper().strip()
    if not sym or len(sym) > 12:
        return False
    now = int(time.time())
    existing = conn.execute(
        f"SELECT id FROM {_TABLE} WHERE symbol = ?",
        (sym,),
    ).fetchone()
    if existing:
        conn.execute(
            f"UPDATE {_TABLE} SET chat_id = ?, added_at = ? WHERE symbol = ?",
            (chat_id, now, sym),
        )
        return True
    insert_returning_id(
        conn,
        f"INSERT INTO {_TABLE} (symbol, chat_id, added_at) VALUES (?, ?, ?)",
        (sym, chat_id, now),
    )
    return True


def list_watchlist_symbols_g36(conn: Any) -> list[str]:
    rows = conn.execute(f"SELECT symbol FROM {_TABLE} ORDER BY added_at DESC").fetchall()
    return [str(r["symbol"]) for r in rows]


def format_watchlist_g36(conn: Any) -> str:
    rows = conn.execute(f"SELECT symbol FROM {_TABLE} ORDER BY added_at DESC").fetchall()
    if not rows:
        return "Watchlist empty. Use /watch SOL"

    lines = ["Watch List", ""]
    for r in rows:
        sym = str(r["symbol"])
        cand = conn.execute(
            """
            SELECT confidence, market_score, candidate_state
            FROM market_candidate_g31
            WHERE symbol = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (sym,),
        ).fetchone()
        conf = f"{float(cand['confidence']):.1f}" if cand and cand["confidence"] is not None else "—"
        ms = str(int(cand["market_score"])) if cand and cand["market_score"] is not None else "—"
        state = str(cand["candidate_state"] or "waiting") if cand else "waiting"
        status = "Waiting" if state in ("rejected", "waiting", "insufficient_data") else state.title()

        lines.extend([
            sym,
            status,
            "",
            "Confidence",
            conf,
            "",
            "Market Score",
            ms,
            "",
        ])
    return "\n".join(lines)
