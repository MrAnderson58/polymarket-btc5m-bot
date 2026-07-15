"""Phase S2.3 — Telegram Signal Inbox (parse + persist + RO decision)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures.parser import _normalize_side, _parse_entry_range, _parse_take_profits
from bot.research.futures.parser_v2 import SL_RE, extract_symbol_v2, parse_signal_v2

# Broader tickers for inbox (SXT, SPCX, …) — not limited to taxonomy allowlist.
_SIDE_RE = re.compile(
    r"\b(LONG|SHORT|BUY|SELL|ЛОНГ|ШОРТ|лонг|шорт)\b",
    re.IGNORECASE,
)
_TICKER_RE = re.compile(
    r"(?:^|[\s\n])(?:\$|#)?\s*([A-Za-z]{2,15})\b",
)
_HASH_DOLLAR = re.compile(r"[$#]\s*([A-Za-z]{2,15})\b")
_HEADER_PAIR = re.compile(
    r"(?im)^\s*[$#]?\s*([A-Za-z]{2,15})\s+(LONG|SHORT|BUY|SELL|ЛОНГ|ШОРТ|лонг|шорт)"
    r"|(LONG|SHORT|BUY|SELL|ЛОНГ|ШОРТ|лонг|шорт)\s+[$#]?\s*([A-Za-z]{2,15})",
)


@dataclass
class ParsedInboxSignalS23:
    symbol: str | None = None
    direction: str | None = None  # LONG | SHORT
    entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    parsed_ok: bool = False
    parser_reason: str = ""
    fields_found: list[str] = field(default_factory=list)


def _norm_direction(raw: str | None) -> str | None:
    if not raw:
        return None
    side = _normalize_side(raw)
    if side in ("LONG", "SHORT"):
        return side
    up = raw.upper()
    if up in ("BUY", "LONG", "ЛОНГ"):
        return "LONG"
    if up in ("SELL", "SHORT", "ШОРТ"):
        return "SHORT"
    return None


def _extract_direction(text: str) -> str | None:
    m = _SIDE_RE.search(text)
    if not m:
        return None
    return _norm_direction(m.group(1))


def _extract_symbol_broad(text: str) -> str | None:
    header = "\n".join(text.strip().splitlines()[:8])
    m = _HEADER_PAIR.search(header) or _HEADER_PAIR.search(text[:400])
    if m:
        sym = (m.group(1) or m.group(4) or "").upper()
        if sym and sym not in ("LONG", "SHORT", "BUY", "SELL", "ЛОНГ", "ШОРТ"):
            return sym.replace("USDT", "")
    for pat in (_HASH_DOLLAR,):
        hm = pat.search(header) or pat.search(text[:400])
        if hm:
            return hm.group(1).upper().replace("USDT", "")
    # Bare ticker on first line: SXT SHORT / BTC LONG
    first = text.strip().splitlines()[0] if text.strip() else ""
    tm = re.match(r"^\s*[$#]?\s*([A-Za-z]{2,15})\b", first)
    if tm:
        cand = tm.group(1).upper()
        if cand not in ("LONG", "SHORT", "BUY", "SELL", "ENTRY", "TP", "SL"):
            return cand.replace("USDT", "")
    return None


def parse_signal_inbox_s23(text: str) -> ParsedInboxSignalS23:
    """Parse manual Telegram signal. Never raises — always returns a result."""
    out = ParsedInboxSignalS23()
    raw = (text or "").strip()
    if not raw:
        out.parser_reason = "empty_text"
        out.parsed_ok = False
        return out

    # Primary: parser_v2
    v2 = parse_signal_v2(raw)
    if v2.parsed.symbol:
        out.symbol = str(v2.parsed.symbol).upper().replace("USDT", "")
        out.fields_found.append("symbol")
    if v2.parsed.side:
        out.direction = _norm_direction(v2.parsed.side)
        if out.direction:
            out.fields_found.append("direction")

    # Broad fallback for unknown tickers (#SXT, $SPCX, …)
    if not out.symbol:
        out.symbol = _extract_symbol_broad(raw)
        if out.symbol:
            out.fields_found.append("symbol_broad")
    if not out.direction:
        out.direction = _extract_direction(raw)
        if out.direction:
            out.fields_found.append("direction_broad")

    # Also try extract_symbol_v2 alone
    if not out.symbol:
        sym, _ = extract_symbol_v2(raw)
        if sym:
            out.symbol = sym.upper().replace("USDT", "")
            out.fields_found.append("symbol_v2")

    emin, emax = _parse_entry_range(raw)
    if emin is not None:
        out.entry = float(emin if emax is None else (emin + emax) / 2.0)
        out.fields_found.append("entry")

    sl = SL_RE.search(raw)
    if sl:
        try:
            out.stop = float(sl.group(1))
            out.fields_found.append("stop")
        except ValueError:
            pass

    tps = _parse_take_profits(raw)
    if tps:
        out.tp1 = tps[0] if len(tps) > 0 else None
        out.tp2 = tps[1] if len(tps) > 1 else None
        out.tp3 = tps[2] if len(tps) > 2 else None
        out.fields_found.append("tp")

    missing: list[str] = []
    if not out.symbol:
        missing.append("symbol")
    if not out.direction:
        missing.append("direction")

    if missing:
        out.parsed_ok = False
        out.parser_reason = "missing:" + ",".join(missing)
    else:
        out.parsed_ok = True
        out.parser_reason = "ok"
        if out.entry is None:
            out.parser_reason = "ok_partial_no_entry"

    return out


def map_decision_label_s23(decision: str | None) -> str:
    d = (decision or "FLAT").upper()
    if d == "LONG":
        return "BUY"
    if d == "SHORT":
        return "SELL"
    return "WAIT"


def format_signal_received_reply_s23(
    *,
    symbol: str | None,
    direction: str | None,
    decision_label: str,
    probability: int | None,
    reason_lines: list[str],
    parsed_ok: bool,
    parser_reason: str,
) -> str:
    if not parsed_ok:
        return "\n".join([
            "📥 SIGNAL RECEIVED",
            "",
            "Parsed",
            "NO",
            "",
            f"Reason",
            parser_reason or "unrecognized",
            "",
            "Saved to Signal Inbox for review.",
        ])

    lines = [
        "✅ SIGNAL RECEIVED",
        "",
        "Symbol",
        str(symbol or "—"),
        "",
        "Direction",
        str(direction or "—"),
        "",
        "Decision",
        decision_label,
        "",
        "Probability",
        f"{int(probability or 0)}%",
        "",
        "Reason",
    ]
    for r in reason_lines[:6]:
        lines.append(str(r))
    if not reason_lines:
        lines.append("—")
    return "\n".join(lines)


def persist_signal_inbox_s23(
    conn: Any,
    *,
    raw_text: str,
    telegram_user: str | None,
    chat_id: int | None,
    parsed: ParsedInboxSignalS23,
    decision_label: str | None = None,
    decision_probability: int | None = None,
    decision_summary: str | None = None,
    decision_run_id: int | None = None,
    source: str = "telegram",
    status: str | None = None,
) -> int:
    now = int(time.time())
    st = status or ("parsed" if parsed.parsed_ok else "rejected")
    if parsed.parsed_ok and decision_label:
        st = "decided"
    cur = conn.execute(
        """
        INSERT INTO market_signal_inbox_s23 (
          received_at, telegram_user, chat_id, raw_text,
          symbol, direction, entry, stop, tp1, tp2, tp3,
          parsed_ok, parser_reason, decision_run_id,
          decision_label, decision_probability, decision_summary,
          status, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            telegram_user,
            chat_id,
            raw_text,
            parsed.symbol,
            parsed.direction,
            parsed.entry,
            parsed.stop,
            parsed.tp1,
            parsed.tp2,
            parsed.tp3,
            1 if parsed.parsed_ok else 0,
            parsed.parser_reason,
            decision_run_id,
            decision_label,
            decision_probability,
            decision_summary,
            st,
            source,
        ),
    )
    return int(cur.lastrowid)


def run_inbox_decision_ro_s23(conn: Any, symbol: str) -> dict[str, Any]:
    """Call Decision Engine READ ONLY — never INSERT decision runs."""
    from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
        run_decision_engine_s20,
    )
    return run_decision_engine_s20(conn, symbol, persist=False, force_fallback=False)


def _reason_lines_from_decision(result: dict[str, Any]) -> list[str]:
    market = result.get("market") or {}
    news = result.get("news") or {}
    decision = result.get("decision") or {}
    lines: list[str] = []
    m_dir = market.get("direction")
    n_sent = news.get("sentiment")
    if m_dir and n_sent:
        if (m_dir == "LONG" and n_sent == "bearish") or (m_dir == "SHORT" and n_sent == "bullish"):
            lines.append(f"News {n_sent}")
            lines.append(f"Trend {m_dir.lower()}")
            lines.append("Conflict")
        else:
            lines.append(f"Market {m_dir}")
            lines.append(f"News {n_sent}")
    summary = str(decision.get("summary") or "").strip()
    if summary:
        lines.append(summary[:180])
    for r in (decision.get("risks") or [])[:2]:
        lines.append(str(r))
    return lines


def process_telegram_signal_inbox_s23(
    *,
    raw_text: str,
    chat_id: int,
    message_id: int | None = None,
    telegram_user: str | None = None,
) -> tuple[str, int | None]:
    """Parse → always persist → optional Decision (no decision INSERT) → reply text.

    Returns (reply_text, inbox_id).
    """
    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.event_schema import apply_migrations

    _ = message_id
    parsed = parse_signal_inbox_s23(raw_text)
    decision_label = "WAIT"
    probability: int | None = None
    summary: str | None = None
    reason_lines: list[str] = []

    with market_events_connection() as conn:
        apply_migrations(conn)

        if parsed.parsed_ok and parsed.symbol:
            try:
                # persist=False inside Decision Engine — no INSERT to decision tables.
                result = run_inbox_decision_ro_s23(conn, parsed.symbol)
                dec = result.get("decision") or {}
                decision_label = map_decision_label_s23(str(dec.get("decision")))
                probability = int(dec.get("probability") or 0)
                summary = str(dec.get("summary") or "")[:500]
                reason_lines = _reason_lines_from_decision(result)
                if result.get("run_id") is not None:
                    # Belt-and-suspenders: inbox path must never create decision runs.
                    raise RuntimeError("decision engine persisted unexpectedly")
            except Exception as exc:
                decision_label = "WAIT"
                probability = 0
                summary = f"decision_error: {exc}"
                reason_lines = [f"Decision engine error: {exc}"]

        inbox_id = persist_signal_inbox_s23(
            conn,
            raw_text=raw_text,
            telegram_user=telegram_user,
            chat_id=chat_id,
            parsed=parsed,
            decision_label=decision_label if parsed.parsed_ok else None,
            decision_probability=probability if parsed.parsed_ok else None,
            decision_summary=summary if parsed.parsed_ok else None,
            decision_run_id=None,
            source="telegram",
        )
        conn.commit()

    reply = format_signal_received_reply_s23(
        symbol=parsed.symbol,
        direction=parsed.direction,
        decision_label=decision_label,
        probability=probability,
        reason_lines=reason_lines,
        parsed_ok=parsed.parsed_ok,
        parser_reason=parsed.parser_reason,
    )
    return reply, inbox_id


def list_signal_inbox_s23(
    conn: Any,
    *,
    limit: int = 20,
    symbol: str | None = None,
    parsed: bool | None = None,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if parsed is True:
        clauses.append("parsed_ok = 1")
    elif parsed is False:
        clauses.append("parsed_ok = 0")
    if decision:
        clauses.append("decision_label = ?")
        params.append(decision.upper())
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM market_signal_inbox_s23
        {where}
        ORDER BY id DESC LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def format_signal_inbox_cli_s23(
    conn: Any,
    *,
    limit: int = 20,
    symbol: str | None = None,
) -> str:
    rows = list_signal_inbox_s23(conn, limit=limit, symbol=symbol)
    if not rows:
        return "Signal Inbox empty."
    lines = [f"Signal Inbox (last {len(rows)})", ""]
    for r in rows:
        lines.extend([
            f"#{r['id']} {r.get('symbol') or '—'} {r.get('direction') or '—'}",
            f"  parsed={'OK' if r.get('parsed_ok') else 'NO'} ({r.get('parser_reason')})",
            f"  decision={r.get('decision_label') or '—'} "
            f"p={r.get('decision_probability') if r.get('decision_probability') is not None else '—'}",
            f"  status={r.get('status')} source={r.get('source')}",
            f"  raw={(r.get('raw_text') or '')[:80]}".replace("\n", " "),
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def signal_inbox_dashboard_s23(
    conn: Any,
    *,
    parsed: str | None = None,
    decision: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    parsed_flag: bool | None = None
    if parsed is not None:
        pl = parsed.lower()
        if pl in ("1", "true", "yes", "ok", "parsed"):
            parsed_flag = True
        elif pl in ("0", "false", "no", "rejected"):
            parsed_flag = False
    rows = list_signal_inbox_s23(
        conn, limit=limit, parsed=parsed_flag, decision=decision,
    )
    counts = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN parsed_ok = 1 THEN 1 ELSE 0 END) AS parsed_n,
          SUM(CASE WHEN parsed_ok = 0 THEN 1 ELSE 0 END) AS rejected_n,
          SUM(CASE WHEN decision_label = 'BUY' THEN 1 ELSE 0 END) AS buy_n,
          SUM(CASE WHEN decision_label = 'SELL' THEN 1 ELSE 0 END) AS sell_n,
          SUM(CASE WHEN decision_label = 'WAIT' THEN 1 ELSE 0 END) AS wait_n
        FROM market_signal_inbox_s23
        """,
    ).fetchone()
    return {
        "tab": "Signal Inbox",
        "filters": {"parsed": parsed, "decision": decision, "limit": limit},
        "counts": dict(counts) if counts else {},
        "signals": rows,
    }
