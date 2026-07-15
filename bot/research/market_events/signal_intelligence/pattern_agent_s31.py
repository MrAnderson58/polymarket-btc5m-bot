"""Phase S3.1 — Pattern Agent (no LLM, SELECT-only over existing research tables)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.pattern_keys_s31 import (
    canonical_pattern_key_s31,
    display_family_s31,
    expand_pattern_key_aliases_s31,
    infer_family_from_market_s31,
    normalize_family_s31,
    normalize_timeframe_s31,
    parse_pattern_key_s31,
    pattern_key_match_s31,
    timeframe_to_minutes_s31,
)

logger = logging.getLogger(__name__)

MIN_SAMPLES_FOUND = 5


@dataclass(frozen=True)
class PatternHitS31:
    symbol: str
    win: bool
    rr: float | None
    hold_hours: float | None
    source: str


def _is_win_outcome(row: Any) -> bool:
    if row["would_hit_tp"]:
        return True
    return float(row["max_profit_pct"] or 0) > 0


def _safe_table_exists(conn: Any, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _collect_candidate_outcomes(
    conn: Any,
    *,
    symbol: str | None,
    direction: str | None,
    limit: int = 2000,
) -> list[PatternHitS31]:
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
            SELECT c.symbol, c.direction, o.would_hit_tp, o.max_profit_pct,
                   o.best_rr, c.rr, o.created_at, o.updated_at
            FROM market_candidate_outcomes_g32 o
            JOIN market_candidate_g31 c ON c.id = o.candidate_id
            WHERE {" AND ".join(clauses)}
              AND (o.would_hit_tp IS NOT NULL OR o.max_profit_pct IS NOT NULL)
            ORDER BY o.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    except Exception as exc:
        logger.debug("pattern candidate outcomes skipped: %s", exc)
        return []

    hits: list[PatternHitS31] = []
    for r in rows:
        hold = None
        try:
            ca, ua = int(r["created_at"] or 0), int(r["updated_at"] or 0)
            if ua > ca > 0:
                hold = round((ua - ca) / 3600.0, 2)
        except (TypeError, ValueError):
            pass
        rr = r["best_rr"] if r["best_rr"] is not None else r["rr"]
        hits.append(
            PatternHitS31(
                symbol=str(r["symbol"]).upper(),
                win=_is_win_outcome(r),
                rr=float(rr) if rr is not None else None,
                hold_hours=hold,
                source="g32",
            )
        )
    return hits


def _collect_validation_hits(
    conn: Any,
    *,
    symbol: str | None,
    direction: str | None,
    limit: int = 1000,
) -> list[PatternHitS31]:
    if not _safe_table_exists(conn, "market_validation_records_g4"):
        return []
    clauses = ["1=1"]
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if direction:
        clauses.append("UPPER(direction) = ?")
        params.append(direction.upper())
    params.append(limit)
    try:
        rows = conn.execute(
            f"""
            SELECT symbol, direction, is_win, pnl_pct, rr
            FROM market_validation_records_g4
            WHERE {" AND ".join(clauses)}
            ORDER BY id DESC LIMIT ?
            """,
            params,
        ).fetchall()
    except Exception as exc:
        logger.debug("pattern validation skipped: %s", exc)
        return []
    return [
        PatternHitS31(
            symbol=str(r["symbol"]).upper(),
            win=bool(r["is_win"]) or float(r["pnl_pct"] or 0) > 0,
            rr=float(r["rr"]) if r["rr"] is not None else None,
            hold_hours=None,
            source="g4",
        )
        for r in rows
    ]


def _collect_learning_hits(
    conn: Any,
    *,
    symbol: str,
    family: str,
    timeframe: str,
) -> tuple[list[PatternHitS31], float | None, int]:
    """Match g1_pattern_stats via compatibility keys. Returns hits-like + rate + samples."""
    if not _safe_table_exists(conn, "market_events_g1_pattern_stats"):
        return [], None, 0
    try:
        rows = conn.execute(
            """
            SELECT pattern_key, samples, reversal_rate, avg_candles_to_reversal
            FROM market_events_g1_pattern_stats
            WHERE pattern_key LIKE ?
            ORDER BY samples DESC
            """,
            (f"{symbol.upper()}|%",),
        ).fetchall()
    except Exception as exc:
        logger.debug("pattern learning skipped: %s", exc)
        return [], None, 0

    matched_samples = 0
    weighted_wr = 0.0
    hold_hours: list[float] = []
    for r in rows:
        key = str(r["pattern_key"] or "")
        if not pattern_key_match_s31(
            key, want_symbol=symbol, want_family=family, want_tf=timeframe,
        ):
            continue
        n = int(r["samples"] or 0)
        if n <= 0:
            continue
        rate = float(r["reversal_rate"] or 0)
        matched_samples += n
        weighted_wr += rate * n
        avg_c = float(r["avg_candles_to_reversal"] or 0)
        if avg_c > 0:
            hold_hours.append(avg_c * 5.0 / 60.0)  # 5m candles → hours

    if matched_samples <= 0:
        return [], None, 0
    wr = weighted_wr / matched_samples
    # Expand into synthetic hits for sample_size accounting (not double-counted if we merge carefully)
    wins = int(round(wr * matched_samples))
    hits = [
        PatternHitS31(symbol=symbol.upper(), win=True, rr=None, hold_hours=None, source="g1")
        for _ in range(wins)
    ] + [
        PatternHitS31(symbol=symbol.upper(), win=False, rr=None, hold_hours=None, source="g1")
        for _ in range(max(0, matched_samples - wins))
    ]
    avg_hold = sum(hold_hours) / len(hold_hours) if hold_hours else None
    # stash avg_hold on first hit via return side channel
    return hits, avg_hold, matched_samples


def _aggregate_hits(
    hits: list[PatternHitS31],
    *,
    symbol: str,
    learning_hold: float | None = None,
) -> dict[str, Any]:
    n = len(hits)
    if n <= 0:
        return {
            "sample_size": 0,
            "historical_wr": 0.0,
            "avg_rr": 0.0,
            "avg_hold_hours": 0.0,
            "similar_symbols": [],
            "confidence": 0.0,
        }
    wins = sum(1 for h in hits if h.win)
    wr = wins / n
    rrs = [h.rr for h in hits if h.rr is not None and h.rr > 0]
    avg_rr = round(sum(rrs) / len(rrs), 2) if rrs else 0.0
    holds = [h.hold_hours for h in hits if h.hold_hours is not None]
    if holds:
        avg_hold = round(sum(holds) / len(holds), 2)
    elif learning_hold is not None:
        avg_hold = round(learning_hold, 2)
    else:
        avg_hold = 0.0

    # Similar symbols: other symbols appearing with wins in same direction cohort
    by_sym: dict[str, list[bool]] = defaultdict(list)
    for h in hits:
        by_sym[h.symbol].append(h.win)
    ranked = sorted(
        by_sym.items(),
        key=lambda kv: (sum(kv[1]) / max(1, len(kv[1])), len(kv[1])),
        reverse=True,
    )
    similar = [s for s, _ in ranked if s != symbol.upper()][:5]
    if symbol.upper() not in similar:
        similar = [symbol.upper()] + similar
    similar = similar[:5]

    # Confidence: grows with sample size and clarity of WR away from 0.5
    size_comp = min(1.0, n / 80.0)
    edge = abs(wr - 0.5) * 2.0
    confidence = round(min(0.95, 0.35 + 0.4 * size_comp + 0.25 * edge), 3)

    return {
        "sample_size": n,
        "historical_wr": round(wr, 3),
        "avg_rr": avg_rr,
        "avg_hold_hours": avg_hold,
        "similar_symbols": similar,
        "confidence": confidence,
    }


def run_pattern_agent_s31(
    conn: Any,
    *,
    symbol: str,
    direction: str | None = None,
    timeframe: str | int | None = "60m",
    market_snapshot: dict[str, Any] | None = None,
    family: str | None = None,
) -> dict[str, Any]:
    """SELECT-only pattern intelligence. No INSERT/UPDATE."""
    sym = (symbol or "BTC").upper().replace("USDT", "").strip()
    direction_u = (direction or (market_snapshot or {}).get("direction") or "").upper() or None
    if direction_u not in ("LONG", "SHORT"):
        direction_u = None
    tf = normalize_timeframe_s31(timeframe)
    fam = normalize_family_s31(
        family or infer_family_from_market_s31(market_snapshot, direction_u)
    )
    pattern_key = canonical_pattern_key_s31(symbol=sym, family=fam, timeframe=tf)

    # Prefer same-symbol+direction outcomes; also gather peers for similar_symbols
    own = _collect_candidate_outcomes(conn, symbol=sym, direction=direction_u)
    peers = _collect_candidate_outcomes(conn, symbol=None, direction=direction_u, limit=800)
    val_own = _collect_validation_hits(conn, symbol=sym, direction=direction_u)
    learn_hits, learn_hold, _learn_n = _collect_learning_hits(
        conn, symbol=sym, family=fam, timeframe=tf,
    )

    # Dedup strategy: use own g32+g4 primarily; add learning if own thin; peers only for symbols list
    primary = list(own) + list(val_own)
    if len(primary) < MIN_SAMPLES_FOUND and learn_hits:
        primary = primary + learn_hits
    if len(primary) < MIN_SAMPLES_FOUND:
        # broaden: same family via peers outcomes (direction match)
        primary = primary + [h for h in peers if h.symbol != sym][: max(0, MIN_SAMPLES_FOUND * 3 - len(primary))]

    agg = _aggregate_hits(primary, symbol=sym, learning_hold=learn_hold)
    # Enrich similar_symbols from peer cohort
    if peers:
        peer_agg = _aggregate_hits(peers + own, symbol=sym)
        sim = peer_agg["similar_symbols"]
        if sim:
            agg["similar_symbols"] = sim

    found = agg["sample_size"] >= MIN_SAMPLES_FOUND
    reason = (
        f"{agg['sample_size']} similar historical setups"
        if found
        else (
            f"Insufficient history ({agg['sample_size']} < {MIN_SAMPLES_FOUND})"
            if agg["sample_size"]
            else "No historical setups found for this pattern key"
        )
    )

    confidence = agg["confidence"] if found else round(agg["confidence"] * 0.5, 3)

    from bot.research.market_events.signal_intelligence.pattern_evidence_s32 import (
        build_pattern_evidence_s32,
    )
    evidence = build_pattern_evidence_s32(
        conn,
        symbol=sym,
        direction=direction_u,
        sample_size=int(agg["sample_size"]),
        confidence=float(confidence),
    )

    return {
        "pattern_found": found,
        "pattern_key": pattern_key,
        "pattern_family": fam,
        "pattern_label": display_family_s31(fam),
        "timeframe": tf,
        "direction": direction_u,
        "sample_size": agg["sample_size"],
        "historical_wr": agg["historical_wr"],
        "avg_rr": agg["avg_rr"],
        "avg_hold_hours": agg["avg_hold_hours"],
        "similar_symbols": agg["similar_symbols"],
        "confidence": confidence,
        "reason": reason,
        "pattern_examples": evidence.get("pattern_examples") or [],
        "common_features": evidence.get("common_features") or [],
        "pattern_quality": evidence.get("pattern_quality") or "Low",
        "meta": {
            "aliases": expand_pattern_key_aliases_s31(pattern_key)[:8],
            "sources_used": sorted({h.source for h in primary}),
            "read_only": True,
            "evidence_variance": evidence.get("evidence_variance"),
            "evidence_n": evidence.get("evidence_n"),
        },
    }


def build_pattern_index_s31(conn: Any, *, limit_per_bucket: int = 500) -> dict[str, Any]:
    """Compute pattern index summary from existing tables (SELECT-only, no INSERT)."""
    buckets: dict[str, list[PatternHitS31]] = defaultdict(list)

    # Candidates with outcomes — bucket by symbol + inferred family from state
    if _safe_table_exists(conn, "market_candidate_outcomes_g32"):
        try:
            rows = conn.execute(
                """
                SELECT c.symbol, c.direction, o.would_hit_tp, o.max_profit_pct,
                       o.best_rr, c.rr, o.created_at, o.updated_at
                FROM market_candidate_outcomes_g32 o
                JOIN market_candidate_g31 c ON c.id = o.candidate_id
                WHERE c.direction IS NOT NULL
                ORDER BY o.id DESC LIMIT 5000
                """,
            ).fetchall()
            for r in rows:
                sym = str(r["symbol"]).upper()
                direction = str(r["direction"] or "").upper()
                fam = "trend_reversal"
                # Rejection "No reversal confirmation" cohort → trend_reversal
                key = canonical_pattern_key_s31(symbol=sym, family=fam, timeframe="60m")
                hold = None
                ca, ua = int(r["created_at"] or 0), int(r["updated_at"] or 0)
                if ua > ca > 0:
                    hold = round((ua - ca) / 3600.0, 2)
                rr = r["best_rr"] if r["best_rr"] is not None else r["rr"]
                buckets[key].append(
                    PatternHitS31(
                        symbol=sym,
                        win=_is_win_outcome(r),
                        rr=float(rr) if rr is not None else None,
                        hold_hours=hold,
                        source="g32",
                    )
                )
        except Exception as exc:
            logger.debug("pattern-build g32 skipped: %s", exc)

    if _safe_table_exists(conn, "market_events_g1_pattern_stats"):
        try:
            rows = conn.execute(
                "SELECT pattern_key, samples, reversal_rate, avg_candles_to_reversal "
                "FROM market_events_g1_pattern_stats"
            ).fetchall()
            for r in rows:
                parsed = parse_pattern_key_s31(str(r["pattern_key"]))
                key = canonical_pattern_key_s31(
                    symbol=parsed["symbol"],
                    family=parsed["family"],
                    timeframe=parsed["timeframe"],
                )
                n = int(r["samples"] or 0)
                wr = float(r["reversal_rate"] or 0)
                wins = int(round(wr * n))
                for _ in range(wins):
                    buckets[key].append(
                        PatternHitS31(parsed["symbol"], True, None, None, "g1")
                    )
                for _ in range(max(0, n - wins)):
                    buckets[key].append(
                        PatternHitS31(parsed["symbol"], False, None, None, "g1")
                    )
        except Exception as exc:
            logger.debug("pattern-build g1 skipped: %s", exc)

    index_rows: list[dict[str, Any]] = []
    for key, hits in buckets.items():
        if not hits:
            continue
        # Cap synthetic inflation
        if len(hits) > limit_per_bucket:
            hits = hits[:limit_per_bucket]
        parsed = parse_pattern_key_s31(key)
        agg = _aggregate_hits(hits, symbol=parsed["symbol"])
        index_rows.append({
            "pattern_key": key,
            "family": parsed["family"],
            "symbol": parsed["symbol"],
            "timeframe": parsed["timeframe"],
            **agg,
            "pattern_found": agg["sample_size"] >= MIN_SAMPLES_FOUND,
        })

    index_rows.sort(key=lambda r: (r["sample_size"], r["historical_wr"]), reverse=True)
    return {
        "buckets": len(index_rows),
        "patterns": index_rows,
        "note": "SELECT-only index over g32/g1 (no INSERT)",
    }


def format_pattern_report_s31(result: dict[str, Any], *, show_examples: bool = False) -> str:
    from bot.research.market_events.signal_intelligence.pattern_evidence_s32 import (
        format_evidence_block_s32,
    )

    lines = [
        "Pattern",
        "",
        str(result.get("pattern_label") or result.get("pattern_family") or "—"),
        "",
        "Key",
        str(result.get("pattern_key") or "—"),
        "",
        "Samples",
        "",
        str(int(result.get("sample_size") or 0)),
        "",
        "WR",
        "",
        f"{round(float(result.get('historical_wr') or 0) * 100):.0f}%",
        "",
        "Avg RR",
        "",
        str(result.get("avg_rr") or 0),
        "",
        "Average hold",
        "",
        f"{result.get('avg_hold_hours') or 0}h",
        "",
        "Confidence",
        "",
        str(result.get("confidence") or 0),
        "",
        "Similar",
        "",
        ", ".join(result.get("similar_symbols") or []) or "—",
        "",
        "Reason",
        "",
        str(result.get("reason") or "—"),
        "",
        "Found" if result.get("pattern_found") else "Not found",
        "",
        "Quality",
        "",
        str(result.get("pattern_quality") or "Low"),
        "",
        format_evidence_block_s32(result, show_examples=show_examples),
        "",
        "READ ONLY",
    ]
    return "\n".join(lines)


def format_pattern_build_report_s31(index: dict[str, Any]) -> str:
    lines = [
        "Pattern Index (S3.1)",
        "",
        f"Buckets: {index.get('buckets', 0)}",
        str(index.get("note") or ""),
        "",
    ]
    for row in (index.get("patterns") or [])[:25]:
        lines.extend([
            str(row.get("pattern_key")),
            f"  samples={row.get('sample_size')} WR={round(float(row.get('historical_wr') or 0)*100):.0f}% "
            f"RR={row.get('avg_rr')} conf={row.get('confidence')}",
            "",
        ])
    if not index.get("patterns"):
        lines.append("No patterns aggregated yet (need g32 outcomes / g1 stats).")
    return "\n".join(lines).rstrip() + "\n"


def format_pattern_block_for_decision_s31(pattern: dict[str, Any]) -> str:
    if not pattern:
        return "Pattern\n—\n"
    wr_pct = round(float(pattern.get("historical_wr") or 0) * 100)
    from bot.research.market_events.signal_intelligence.pattern_evidence_s32 import (
        format_evidence_block_s32,
    )
    return "\n".join([
        "Pattern",
        "",
        str(pattern.get("pattern_label") or "—"),
        "",
        f"WR {wr_pct}%",
        "",
        f"{int(pattern.get('sample_size') or 0)} samples",
        "",
        f"Confidence {pattern.get('confidence')}",
        "",
        f"Quality {pattern.get('pattern_quality') or '—'}",
        "",
        "Reason",
        "",
        str(pattern.get("reason") or "—"),
        "",
        format_evidence_block_s32(pattern, show_examples=False),
    ])
