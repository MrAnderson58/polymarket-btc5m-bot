"""MTF clean research eligibility filter — valid quotes, alignment, strike, no look-ahead."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from bot.research.mtf.alignment import is_15m_window_correct
from bot.research.mtf.discovery import parse_15m_window_start_ts
from bot.research.mtf.quote_audit import BID_GT_ASK
from bot.research.mtf.snapshots import TABLE

# Set MTF_QUOTE_FIX_TS env to unix timestamp when quote-mapping fix deployed.
from bot.research.bidirectional_v13_execution.config import MTF_QUOTE_FIX_TS


def row_is_clean_15m(row: sqlite3.Row) -> tuple[bool, str | None]:
    """Return (eligible, reject_reason). Does not mutate data."""
    slug = row["market_15m_slug"]
    if not slug:
        return False, "no_slug"

    ts = int(row["timestamp"])
    ws = parse_15m_window_start_ts(slug)
    if ws is None:
        return False, "bad_slug"
    if not (ws <= ts < ws + 900):
        return False, "wrong_window"

    if not is_15m_window_correct(slug, ts):
        return False, "alignment_fail"

    yb, ya = row["market_15m_yes_bid"], row["market_15m_yes_ask"]
    nb, na = row["market_15m_no_bid"], row["market_15m_no_ask"]
    if ya is None and yb is None:
        return False, "missing_quote"

    for bid, ask in ((yb, ya), (nb, na)):
        if bid is not None and ask is not None and bid > ask:
            return False, BID_GT_ASK

    strike = row["market_15m_strike"]
    if strike is None:
        return False, "missing_strike"

    return True, None


def audit_bid_gt_ask_timeline(
    conn: sqlite3.Connection,
    *,
    fix_ts: int | None = None,
) -> dict[str, Any]:
    """Group BID_GT_ASK rows by era, slug, and hour — read-only."""
    fix_ts = fix_ts if fix_ts is not None else MTF_QUOTE_FIX_TS
    rows = conn.execute(
        f"""
        SELECT id, timestamp, market_15m_slug,
               market_15m_yes_bid, market_15m_yes_ask,
               market_15m_no_bid, market_15m_no_ask
        FROM {TABLE}
        WHERE market_15m_slug IS NOT NULL
        ORDER BY timestamp ASC
        """
    ).fetchall()

    before = after = unknown_era = 0
    by_slug: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    still_creating = 0

    for row in rows:
        yb, ya = row["market_15m_yes_bid"], row["market_15m_yes_ask"]
        nb, na = row["market_15m_no_bid"], row["market_15m_no_ask"]
        bad = False
        for bid, ask in ((yb, ya), (nb, na)):
            if bid is not None and ask is not None and bid > ask:
                bad = True
                break
        if not bad:
            continue

        ts = int(row["timestamp"])
        slug = row["market_15m_slug"] or "UNKNOWN"
        by_slug[slug] = by_slug.get(slug, 0) + 1
        hour_key = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:00")
        by_hour[hour_key] = by_hour.get(hour_key, 0) + 1

        if fix_ts is None:
            unknown_era += 1
        elif ts < fix_ts:
            before += 1
        else:
            after += 1
            still_creating += 1

    top_slugs = sorted(by_slug.items(), key=lambda x: -x[1])[:10]
    top_hours = sorted(by_hour.items(), key=lambda x: -x[1])[:10]

    if fix_ts is None:
        hypothesis = "Set MTF_QUOTE_FIX_TS to split before/after quote-mapping fix."
    elif after == 0:
        hypothesis = "BID_GT_ASK appears legacy-only (none after fix deployment)."
    elif after > 0 and before > 0:
        hypothesis = (
            f"BID_GT_ASK still created after fix ({after} rows) — "
            "investigate collector or stale token cache."
        )
    else:
        hypothesis = "All BID_GT_ASK rows predate fix deployment."

    return {
        "total_bid_gt_ask": before + after + unknown_era,
        "before_fix": before,
        "after_fix": after,
        "unknown_era": unknown_era,
        "fix_ts": fix_ts,
        "top_slugs": top_slugs,
        "top_hours": top_hours,
        "stale_token_hypothesis": hypothesis,
    }


def count_clean_vs_raw(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT * FROM {TABLE} WHERE market_15m_slug IS NOT NULL"
    ).fetchall()
    raw = len(rows)
    clean = sum(1 for r in rows if row_is_clean_15m(r)[0])
    return {"raw_rows": raw, "clean_rows": clean, "rejected": raw - clean}
