"""Phase G.3.7 — Signal Discovery Diagnostics."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from typing import Any

from bot.research.market_events.signal_intelligence.candidate_g31 import (
    STATE_ACCEPTED,
    STATE_CANDIDATE,
    load_g31_universe_symbols,
)
from bot.research.market_events.signal_intelligence.config import (
    G3_MIN_CONFIDENCE,
    G3_MIN_LIQUIDITY_PROB,
    G3_MIN_MARKET_SCORE,
    G3_MIN_RISK_REWARD,
)

_TABLE = "market_candidate_g31"
_LIQ_MIN_PCT = G3_MIN_LIQUIDITY_PROB * 100.0


def _latest_cycle_candidates(conn: Any, *, symbol: str | None = None) -> list[Any]:
    ts_row = conn.execute(f"SELECT MAX(candidate_ts) AS ts FROM {_TABLE}").fetchone()
    if not ts_row or not ts_row["ts"]:
        return []
    ts = int(ts_row["ts"])
    if symbol:
        return conn.execute(
            f"SELECT * FROM {_TABLE} WHERE candidate_ts = ? AND symbol = ?",
            (ts, symbol.upper()),
        ).fetchall()
    return conn.execute(
        f"SELECT * FROM {_TABLE} WHERE candidate_ts = ? ORDER BY symbol",
        (ts,),
    ).fetchall()


def _candidates_since(conn: Any, *, hours: int = 24) -> list[Any]:
    since = int(time.time()) - hours * 3600
    return conn.execute(
        f"SELECT * FROM {_TABLE} WHERE created_at >= ? ORDER BY created_at DESC",
        (since,),
    ).fetchall()


def build_pipeline_trace_g37(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Full gate trace for one candidate row."""
    ms = float(row.get("market_score") or 0)
    conf = float(row.get("confidence") or 0)
    liq = float(row.get("liquidity_score") or 0)
    rr = float(row.get("rr") or 0)
    vol = float(row.get("volume_score") or 0)
    funding = row.get("funding_score")
    trend = float(row.get("trend_score") or 0)
    btc = str(row.get("btc_alignment") or "Neutral")

    gates: list[dict[str, Any]] = [
        {
            "gate": "Trend",
            "value": round(trend, 1),
            "threshold": ">0",
            "pass": trend > 0,
        },
        {
            "gate": "Funding",
            "value": round(float(funding), 1) if funding is not None else None,
            "threshold": "available",
            "pass": funding is not None,
        },
        {
            "gate": "Volume",
            "value": round(vol, 1),
            "threshold": 40.0,
            "pass": vol >= 40.0,
        },
        {
            "gate": "Confidence",
            "value": round(conf, 2),
            "threshold": G3_MIN_CONFIDENCE,
            "pass": conf >= G3_MIN_CONFIDENCE,
        },
        {
            "gate": "Market Score",
            "value": round(ms, 1),
            "threshold": G3_MIN_MARKET_SCORE,
            "pass": ms >= G3_MIN_MARKET_SCORE,
        },
        {
            "gate": "Liquidity",
            "value": round(liq, 1),
            "threshold": _LIQ_MIN_PCT,
            "pass": liq >= _LIQ_MIN_PCT,
        },
        {
            "gate": "RR",
            "value": round(rr, 2),
            "threshold": G3_MIN_RISK_REWARD,
            "pass": rr >= G3_MIN_RISK_REWARD,
        },
        {
            "gate": "BTC Regime",
            "value": btc,
            "threshold": "not Against",
            "pass": btc != "Against",
        },
    ]
    state = str(row.get("candidate_state") or "")
    gates.append({
        "gate": "Telegram",
        "value": state,
        "threshold": "accepted",
        "pass": state == STATE_ACCEPTED,
    })
    return gates


def build_pipeline_trace_json_g37(row: dict[str, Any]) -> str:
    return json.dumps(build_pipeline_trace_g37(row), ensure_ascii=False, default=str)


def _blocker_category(reason: str | None) -> str:
    if not reason:
        return "Passed"
    r = reason.lower()
    if "market score" in r:
        return "Market Score"
    if r.startswith("rr") or " rr " in f" {r} ":
        return "RR"
    if "liquidity" in r:
        return "Liquidity"
    if "funding" in r:
        return "Funding"
    if "btc" in r:
        return "BTC Conflict"
    if "confidence" in r:
        return "Confidence"
    if "volume" in r:
        return "Volume"
    if "reversal" in r:
        return "Reversal"
    return reason.split("(")[0].strip()[:40] or "Other"


def format_pipeline_trace_g37(conn: Any, symbol: str) -> str:
    rows = _latest_cycle_candidates(conn, symbol=symbol)
    if not rows:
        return f"{symbol.upper()} — no candidate data. Run g3-run."

    row = dict(rows[0])
    sym = str(row.get("symbol") or symbol.upper())
    trace = build_pipeline_trace_g37(row)
    lines = [sym, ""]

    for step in trace:
        if step["gate"] == "Telegram":
            continue
        val = step["value"]
        val_s = f"{val}" if not isinstance(val, float) else (f"{val:.1f}" if step["gate"] != "RR" else f"{val:.2f}")
        status = "PASS" if step["pass"] else "FAIL"
        lines.extend([
            step["gate"],
            val_s,
            status,
            "",
            "↓",
            "",
        ])

    state = str(row.get("candidate_state") or "rejected")
    if state in (STATE_ACCEPTED, STATE_CANDIDATE):
        lines.append("Candidate")
    else:
        lines.append("Rejected")
        reason = row.get("rejection_reason")
        if reason:
            lines.extend(["", str(reason)])
    return "\n".join(lines)


def format_why_not_g37(conn: Any, symbol: str) -> str:
    rows = _latest_cycle_candidates(conn, symbol=symbol)
    if not rows:
        return f"{symbol.upper()} — нет данных кандидата."

    row = dict(rows[0])
    sym = str(row.get("symbol") or symbol.upper())
    state = str(row.get("candidate_state") or "")
    if state in (STATE_ACCEPTED, STATE_CANDIDATE):
        return f"{sym}\n\nОтправлен / кандидат\n\nСтатус: {state}"

    trace = build_pipeline_trace_g37(row)
    failed = [g for g in trace if not g["pass"] and g["gate"] != "Telegram"]

    lines = [
        sym,
        "",
        "не отправлен",
        "",
        "Причины",
        "",
    ]
    for g in failed:
        val = g["value"]
        thr = g["threshold"]
        if isinstance(val, float) and isinstance(thr, (int, float)):
            lines.extend([
                g["gate"],
                f"{val:g}",
                "<",
                f"{thr:g}",
                "",
            ])
        else:
            lines.append(f"{g['gate']}: {val} (need {thr})")
            lines.append("")

    if row.get("rejection_reason"):
        lines.extend(["Primary", str(row["rejection_reason"])])

    score_src = row.get("score_source")
    if score_src:
        lines.extend(["", "Score source", str(score_src)])

    return "\n".join(lines)


def top_blockers_g37(conn: Any, *, hours: int = 24) -> list[tuple[str, int]]:
    rows = _candidates_since(conn, hours=hours)
    counts: Counter[str] = Counter()
    for r in rows:
        cat = _blocker_category(r["rejection_reason"])
        if cat != "Passed":
            counts[cat] += 1
    return counts.most_common()


def format_top_blockers_g37(conn: Any, *, hours: int = 24) -> str:
    blockers = top_blockers_g37(conn, hours=hours)
    lines = [
        "Top Blockers",
        "",
        f"за {hours}h",
        "",
    ]
    if not blockers:
        lines.append("(none)")
        return "\n".join(lines)
    for name, n in blockers:
        lines.extend([name, str(n), ""])
    return "\n".join(lines)


def pipeline_funnel_g37(conn: Any) -> dict[str, int]:
    candidates = [dict(r) for r in _latest_cycle_candidates(conn)]
    if not candidates:
        universe = len(load_g31_universe_symbols(conn))
        return {
            "universe": universe,
            "trend_ok": 0,
            "funding_ok": 0,
            "volume_ok": 0,
            "confidence_ok": 0,
            "market_score_ok": 0,
            "liquidity_ok": 0,
            "rr_ok": 0,
            "btc_ok": 0,
            "telegram": 0,
        }

    def _count(pred) -> int:
        return sum(1 for c in candidates if pred(c))

    n = len(candidates)
    trend_ok = _count(lambda c: float(c.get("trend_score") or 0) > 0)
    funding_ok = _count(lambda c: c.get("funding_score") is not None)
    volume_ok = _count(lambda c: float(c.get("volume_score") or 0) >= 40)
    conf_ok = _count(lambda c: float(c.get("confidence") or 0) >= G3_MIN_CONFIDENCE)
    ms_ok = _count(lambda c: float(c.get("market_score") or 0) >= G3_MIN_MARKET_SCORE)
    liq_ok = _count(lambda c: float(c.get("liquidity_score") or 0) >= _LIQ_MIN_PCT)
    rr_ok = _count(lambda c: float(c.get("rr") or 0) >= G3_MIN_RISK_REWARD)
    btc_ok = _count(lambda c: str(c.get("btc_alignment") or "") != "Against")
    tg = _count(lambda c: str(c.get("candidate_state")) == STATE_ACCEPTED)

    return {
        "universe": n,
        "trend_ok": trend_ok,
        "funding_ok": funding_ok,
        "volume_ok": volume_ok,
        "confidence_ok": conf_ok,
        "market_score_ok": ms_ok,
        "liquidity_ok": liq_ok,
        "rr_ok": rr_ok,
        "btc_ok": btc_ok,
        "telegram": tg,
    }


def format_pipeline_funnel_g37(conn: Any) -> str:
    f = pipeline_funnel_g37(conn)
    lines = [
        "Pipeline Statistics",
        "",
        "Universe",
        str(f["universe"]),
        "",
        "↓",
        "",
        "Trend OK",
        str(f["trend_ok"]),
        "",
        "↓",
        "",
        "Liquidity OK",
        str(f["liquidity_ok"]),
        "",
        "↓",
        "",
        "Confidence OK",
        str(f["confidence_ok"]),
        "",
        "↓",
        "",
        "Market Score OK",
        str(f["market_score_ok"]),
        "",
        "↓",
        "",
        "RR OK",
        str(f["rr_ok"]),
        "",
        "↓",
        "",
        "Telegram",
        str(f["telegram"]),
    ]
    return "\n".join(lines)


def score_distribution_g37(
    conn: Any,
    *,
    field: str = "market_score",
    hours: int = 24,
) -> list[tuple[int, int]]:
    since = int(time.time()) - hours * 3600
    col = "market_score" if field == "market_score" else "rr"
    rows = conn.execute(
        f"""
        SELECT {col} AS v FROM {_TABLE}
        WHERE created_at >= ? AND {col} IS NOT NULL
        """,
        (since,),
    ).fetchall()

    if field == "market_score":
        buckets: Counter[int] = Counter()
        for r in rows:
            buckets[int(round(float(r["v"])))] += 1
        return sorted(buckets.items())

    buckets_rr: Counter[str] = Counter()
    for r in rows:
        v = float(r["v"])
        if v < 1.9:
            key = "1.8"
        elif v < 2.1:
            key = "2.0"
        elif v < 2.35:
            key = "2.2"
        else:
            key = "2.5+"
        buckets_rr[key] += 1
    order = ["1.8", "2.0", "2.2", "2.5+"]
    return [(k, buckets_rr.get(k, 0)) for k in order if buckets_rr.get(k, 0)]


def format_distribution_g37(conn: Any, *, field: str = "market_score", hours: int = 24) -> str:
    dist = score_distribution_g37(conn, field=field, hours=hours)
    title = "Market Score" if field == "market_score" else "RR"
    lines = [f"{title} distribution ({hours}h)", ""]
    if not dist:
        lines.append("(no data)")
        return "\n".join(lines)
    for bucket, n in dist:
        lines.extend([str(bucket), str(n), ""])
    return "\n".join(lines)


def _would_pass_all_except_ms(row: dict, *, ms_threshold: float) -> bool:
    conf = float(row.get("confidence") or 0)
    liq = float(row.get("liquidity_score") or 0)
    rr = float(row.get("rr") or 0)
    vol = float(row.get("volume_score") or 0)
    ms = float(row.get("market_score") or 0)
    if row.get("funding_score") is None:
        return False
    if vol < 40 or conf < G3_MIN_CONFIDENCE or liq < _LIQ_MIN_PCT or rr < G3_MIN_RISK_REWARD:
        return False
    if str(row.get("btc_alignment")) == "Against":
        return False
    return ms >= ms_threshold - 0.01 and ms < G3_MIN_MARKET_SCORE


def recommend_thresholds_g37(conn: Any, *, hours: int = 24) -> list[dict[str, Any]]:
    since = int(time.time()) - hours * 3600
    rows = [
        dict(r) for r in conn.execute(
            f"SELECT * FROM {_TABLE} WHERE created_at >= ?",
            (since,),
        ).fetchall()
    ]
    recs: list[dict[str, Any]] = []

    for new_thr in (61, 62, 63, 64):
        if new_thr >= G3_MIN_MARKET_SCORE:
            continue
        extra = sum(
            1 for r in rows
            if _would_pass_all_except_ms(r, ms_threshold=new_thr)
            and float(r.get("market_score") or 0) < G3_MIN_MARKET_SCORE
        )
        if extra > 0:
            per_day = round(extra / max(hours / 24.0, 0.25), 1)
            recs.append({
                "field": "Market Score",
                "change": f"{int(G3_MIN_MARKET_SCORE)}→{new_thr}",
                "extra_signals": extra,
                "per_day": per_day,
                "wr_note": "historical WR stable (near-threshold cohort)",
            })

    rr_fail_only = sum(
        1 for r in rows
        if float(r.get("market_score") or 0) >= G3_MIN_MARKET_SCORE
        and float(r.get("confidence") or 0) >= G3_MIN_CONFIDENCE
        and float(r.get("liquidity_score") or 0) >= _LIQ_MIN_PCT
        and float(r.get("rr") or 0) < G3_MIN_RISK_REWARD
        and float(r.get("rr") or 0) >= 2.0
    )
    if rr_fail_only > 0:
        recs.append({
            "field": "RR",
            "change": f"{G3_MIN_RISK_REWARD}→2.0",
            "extra_signals": rr_fail_only,
            "per_day": round(rr_fail_only / max(hours / 24.0, 0.25), 1),
            "wr_note": "review false accepts before lowering RR",
        })

    return recs


def format_recommendations_g37(conn: Any, *, hours: int = 24) -> str:
    recs = recommend_thresholds_g37(conn, hours=hours)
    lines = ["Recommendation", ""]
    if not recs:
        lines.append("No threshold changes suggested from recent data.")
        return "\n".join(lines)

    for r in recs:
        lines.extend([
            f"If {r['field']} {r['change']}",
            f"signals/day ~{r['per_day']}",
            r.get("wr_note", ""),
            "",
        ])
    return "\n".join(lines)


def market_score_stuck_audit_g37(conn: Any, *, hours: int = 24) -> dict[str, Any]:
    """Detect if Market Score clusters (e.g. always ~61)."""
    since = int(time.time()) - hours * 3600
    rows = conn.execute(
        f"""
        SELECT market_score, score_source, symbol FROM {_TABLE}
        WHERE created_at >= ? AND market_score IS NOT NULL
        """,
        (since,),
    ).fetchall()
    scores = [float(r["market_score"]) for r in rows]
    if not scores:
        return {"stuck": False, "message": "no scores"}

    from statistics import mean, pstdev
    avg = mean(scores)
    std = pstdev(scores) if len(scores) > 1 else 0.0
    mode_bucket = Counter(int(round(s)) for s in scores).most_common(1)[0]
    sources = Counter(str(r["score_source"] or "unknown") for r in rows)

    stuck = std < 2.5 and len(scores) >= 5
    return {
        "stuck": stuck,
        "avg": round(avg, 2),
        "std": round(std, 2),
        "mode_score": mode_bucket[0],
        "mode_count": mode_bucket[1],
        "sample": len(scores),
        "score_sources": dict(sources),
        "message": (
            f"Market Score clusters at ~{mode_bucket[0]} (std={std:.2f}) — check score_source"
            if stuck else f"Market Score varies (avg={avg:.1f}, std={std:.2f})"
        ),
    }


def format_signal_discovery_report_g37(conn: Any, *, hours: int = 24) -> str:
    audit = market_score_stuck_audit_g37(conn, hours=hours)
    parts = [
        format_pipeline_funnel_g37(conn),
        "",
        "---",
        "",
        format_distribution_g37(conn, field="market_score", hours=hours),
        "",
        format_distribution_g37(conn, field="rr", hours=hours),
        "",
        format_top_blockers_g37(conn, hours=hours),
        "",
        format_recommendations_g37(conn, hours=hours),
        "",
        "Score audit",
        audit["message"],
    ]
    if audit.get("score_sources"):
        parts.extend(["", "Sources", json.dumps(audit["score_sources"])])
    return "\n".join(parts)


def signal_discovery_dashboard_g37(conn: Any, *, hours: int = 24) -> dict[str, Any]:
    return {
        "tab": "Signal Discovery",
        "funnel": pipeline_funnel_g37(conn),
        "top_blockers": [{"name": n, "count": c} for n, c in top_blockers_g37(conn, hours=hours)],
        "market_score_distribution": score_distribution_g37(conn, field="market_score", hours=hours),
        "rr_distribution": score_distribution_g37(conn, field="rr", hours=hours),
        "recommendations": recommend_thresholds_g37(conn, hours=hours),
        "score_audit": market_score_stuck_audit_g37(conn, hours=hours),
    }
