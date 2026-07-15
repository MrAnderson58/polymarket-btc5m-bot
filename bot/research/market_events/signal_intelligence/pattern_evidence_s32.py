"""Phase S3.2 — Pattern Evidence Engine (examples, common_features, quality). SELECT-only."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

MAX_EXAMPLES = 10


def _safe_table_exists(conn: Any, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _fmt_date(ts: int | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return "—"


def _exit_price(row: Any) -> float | None:
    for col in ("price_4h", "price_1h", "price_30m", "price_15m", "price_24h"):
        try:
            val = row[col]
        except (KeyError, IndexError, TypeError):
            val = None
        if val is not None:
            return float(val)
    entry = float(row["price_entry"] or 0)
    pnl = float(row["max_profit_pct"] or 0)
    direction = str(row["direction"] or "LONG").upper()
    if entry <= 0:
        return None
    if direction == "SHORT":
        return round(entry * (1.0 - pnl / 100.0), 6)
    return round(entry * (1.0 + pnl / 100.0), 6)


def _row_to_example(row: Any) -> dict[str, Any]:
    entry = float(row["price_entry"]) if row["price_entry"] is not None else None
    exit_px = _exit_price(row)
    pnl = float(row["max_profit_pct"] or 0)
    hold = None
    ca, ua = int(row["created_at"] or 0), int(row["updated_at"] or 0)
    if ua > ca > 0:
        hold = round((ua - ca) / 3600.0, 2)

    funding = float(row["funding_score"]) if row["funding_score"] is not None else None
    oi = float(row["oi_score"]) if row["oi_score"] is not None else None
    volume = float(row["volume_score"]) if row["volume_score"] is not None else None
    atr = float(row["atr_score"]) if row["atr_score"] is not None else None
    fg = float(row["fear_greed"]) if row["fear_greed"] is not None else None

    reasons: list[str] = []
    if row["rejection_reason"]:
        reasons.append(" ".join(str(row["rejection_reason"]).split())[:80])
    if row["would_hit_tp"]:
        reasons.append("hit TP")
    elif pnl > 0:
        reasons.append(f"profit {pnl:+.2f}%")
    else:
        reasons.append(f"loss {pnl:+.2f}%")

    return {
        "symbol": str(row["symbol"]).upper(),
        "date": _fmt_date(ca or None),
        "direction": str(row["direction"] or "—").upper(),
        "entry": entry,
        "exit": exit_px,
        "PnL": round(pnl, 3),
        "hold": hold,
        "funding": funding,
        "OI": oi,
        "volume": volume,
        "ATR": atr,
        "fear_greed": fg,
        "reason": "; ".join(reasons) if reasons else "—",
        "candidate_id": int(row["candidate_id"]) if row["candidate_id"] is not None else None,
        "win": bool(row["would_hit_tp"]) or pnl > 0,
    }


def fetch_pattern_evidence_rows_s32(
    conn: Any,
    *,
    symbol: str | None,
    direction: str | None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """SELECT rich historical cases from g31+g32 (read-only)."""
    if not _safe_table_exists(conn, "market_candidate_outcomes_g32"):
        return []
    clauses = ["1=1"]
    params: list[Any] = []
    if symbol:
        clauses.append("c.symbol = ?")
        params.append(symbol.upper())
    if direction:
        clauses.append("UPPER(c.direction) = ?")
        params.append(direction.upper())
    params.append(limit)
    try:
        rows = conn.execute(
            f"""
            SELECT c.id AS candidate_id, c.symbol, c.direction, c.rejection_reason,
                   c.funding_score, c.oi_score, c.volume_score, c.atr_score, c.fear_greed,
                   o.price_entry, o.price_15m, o.price_30m, o.price_1h, o.price_4h, o.price_24h,
                   o.max_profit_pct, o.would_hit_tp, o.created_at, o.updated_at, o.best_rr
            FROM market_candidate_outcomes_g32 o
            JOIN market_candidate_g31 c ON c.id = o.candidate_id
            WHERE {" AND ".join(clauses)}
              AND o.price_entry IS NOT NULL
            ORDER BY o.max_profit_pct DESC, o.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    except Exception:
        return []
    return [_row_to_example(r) for r in rows]


def select_best_examples_s32(rows: list[dict[str, Any]], *, limit: int = MAX_EXAMPLES) -> list[dict[str, Any]]:
    """Top cases: wins first by PnL, then fill with least-bad losses."""
    wins = sorted([r for r in rows if r.get("win")], key=lambda r: float(r.get("PnL") or 0), reverse=True)
    losses = sorted([r for r in rows if not r.get("win")], key=lambda r: float(r.get("PnL") or 0), reverse=True)
    out = wins[:limit]
    if len(out) < limit:
        out.extend(losses[: limit - len(out)])
    # Public fields only (drop helper)
    public = []
    for r in out[:limit]:
        public.append({
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "direction": r.get("direction"),
            "entry": r.get("entry"),
            "exit": r.get("exit"),
            "PnL": r.get("PnL"),
            "hold": r.get("hold"),
            "funding": r.get("funding"),
            "OI": r.get("OI"),
            "volume": r.get("volume"),
            "ATR": r.get("ATR"),
            "reason": r.get("reason"),
        })
    return public


def compute_common_features_s32(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feature presence counts over evidence rows (score fields from g31)."""
    if not rows:
        return []
    n = len(rows)

    def _ratio(pred) -> tuple[int, int]:
        hits = sum(1 for r in rows if pred(r))
        return hits, n

    checks = [
        ("Funding positive", lambda r: r.get("funding") is not None and float(r["funding"]) >= 55),
        ("OI rising", lambda r: r.get("OI") is not None and float(r["OI"]) >= 60),
        ("Volume expansion", lambda r: r.get("volume") is not None and float(r["volume"]) >= 50),
        ("ATR contraction", lambda r: r.get("ATR") is not None and float(r["ATR"]) <= 55),
        ("FearGreed <30", lambda r: r.get("fear_greed") is not None and float(r["fear_greed"]) < 30),
    ]
    out: list[dict[str, Any]] = []
    for name, pred in checks:
        hits, total = _ratio(pred)
        out.append({
            "name": name,
            "hits": hits,
            "total": total,
            "display": f"{hits}/{total}",
            "rate": round(hits / total, 3) if total else 0.0,
        })
    out.sort(key=lambda x: x["rate"], reverse=True)
    return out


def compute_pnl_variance_s32(rows: list[dict[str, Any]]) -> float:
    pnls = [float(r.get("PnL") or 0) for r in rows]
    if len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls)
    var = sum((p - mean) ** 2 for p in pnls) / len(pnls)
    return round(math.sqrt(var), 3)


def compute_pattern_quality_s32(
    *,
    sample_size: int,
    confidence: float,
    variance: float,
) -> str:
    """High / Medium / Low from sample size, confidence, outcome variance."""
    # Variance of PnL%: low is tighter cluster
    tight = variance <= 2.5
    mid_var = variance <= 5.0
    if sample_size >= 40 and confidence >= 0.70 and tight:
        return "High"
    if sample_size >= 20 and confidence >= 0.55 and mid_var:
        return "Medium"
    if sample_size >= 15 and confidence >= 0.50:
        return "Medium"
    return "Low"


def build_pattern_evidence_s32(
    conn: Any,
    *,
    symbol: str,
    direction: str | None,
    sample_size: int,
    confidence: float,
) -> dict[str, Any]:
    """Assemble examples + common_features + pattern_quality (SELECT-only)."""
    own = fetch_pattern_evidence_rows_s32(conn, symbol=symbol, direction=direction, limit=150)
    rows = own
    if len(rows) < MAX_EXAMPLES and direction:
        peers = fetch_pattern_evidence_rows_s32(conn, symbol=None, direction=direction, limit=100)
        seen = {(r.get("symbol"), r.get("date"), r.get("entry")) for r in rows}
        for p in peers:
            key = (p.get("symbol"), p.get("date"), p.get("entry"))
            if key in seen:
                continue
            rows.append(p)
            seen.add(key)
            if len(rows) >= 80:
                break

    examples = select_best_examples_s32(rows, limit=MAX_EXAMPLES)
    # Features from own symbol when possible, else all evidence rows used for examples pool
    feature_pool = own if len(own) >= 5 else rows
    common = compute_common_features_s32(feature_pool[:50])
    variance = compute_pnl_variance_s32(feature_pool[:80])
    quality = compute_pattern_quality_s32(
        sample_size=sample_size,
        confidence=confidence,
        variance=variance,
    )
    return {
        "pattern_examples": examples,
        "common_features": common,
        "pattern_quality": quality,
        "evidence_variance": variance,
        "evidence_n": len(feature_pool),
    }


def format_evidence_block_s32(evidence: dict[str, Any], *, show_examples: bool = False) -> str:
    lines: list[str] = [
        "Evidence",
        "",
        f"{len(evidence.get('pattern_examples') or [])} similar cases",
        "",
        "Common",
        "",
    ]
    for f in (evidence.get("common_features") or [])[:5]:
        short = f["name"]
        if f["name"] == "Funding positive":
            short = "Funding+"
        elif f["name"] == "OI rising":
            short = "OI+"
        elif f["name"] == "Volume expansion":
            short = "Volume+"
        elif f["name"] == "ATR contraction":
            short = "ATR↓"
        elif f["name"] == "FearGreed <30":
            short = "FG<30"
        lines.append(f"{short}  {f['display']}")
    if show_examples:
        lines.extend(["", "Examples", ""])
        for i, ex in enumerate(evidence.get("pattern_examples") or [], 1):
            lines.extend([
                f"{i}. {ex.get('symbol')} {ex.get('direction')} {ex.get('date')}",
                f"   entry={ex.get('entry')} exit={ex.get('exit')} PnL={ex.get('PnL')} hold={ex.get('hold')}h",
                f"   funding={ex.get('funding')} OI={ex.get('OI')} vol={ex.get('volume')} ATR={ex.get('ATR')}",
                f"   {ex.get('reason')}",
                "",
            ])
    return "\n".join(lines).rstrip()


def pattern_explorer_dashboard_s32(
    conn: Any,
    *,
    symbol: str | None = None,
    direction: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
        build_pattern_index_s31,
        run_pattern_agent_s31,
    )

    if symbol:
        result = run_pattern_agent_s31(
            conn, symbol=symbol.upper(), direction=direction, timeframe="60m",
        )
        return {
            "tab": "Pattern Explorer",
            "symbol": symbol.upper(),
            "pattern": result,
            "examples": result.get("pattern_examples") or [],
            "common_features": result.get("common_features") or [],
            "pattern_quality": result.get("pattern_quality"),
        }

    index = build_pattern_index_s31(conn)
    patterns = (index.get("patterns") or [])[:limit]
    # Attach quality lightly for list view
    enriched = []
    for p in patterns:
        var = 3.0  # unknown at index level
        quality = compute_pattern_quality_s32(
            sample_size=int(p.get("sample_size") or 0),
            confidence=float(p.get("confidence") or 0),
            variance=var,
        )
        enriched.append({**p, "pattern_quality": quality})
    return {
        "tab": "Pattern Explorer",
        "filters": {"symbol": symbol, "direction": direction, "limit": limit},
        "patterns": enriched,
        "buckets": index.get("buckets", 0),
    }
