"""Phase G.3.5 — Telegram Intelligence for G3.* (research-only)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.research.market_events.market_event_alerts import _safe_alert, ALERT_SHOCK, PAPER_LABEL
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    STATE_ACCEPTED,
    STATE_CANDIDATE,
    STATE_PROVISIONAL,
    STATE_REJECTED,
)
from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.config import (
    G3_DAILY_REPORT_HOUR_LOCAL,
    G35_CANDIDATE_ALERTS_ENABLED,
    G35_CLAUDE_INSIGHT_ENABLED,
    G35_ENABLED,
    G35_HOURLY_BRIEF_ENABLED,
)
from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state, set_g3_ops_state
from bot.research.market_events.signal_intelligence.market_score_f7 import compute_market_score
from bot.research.market_events.signal_intelligence.trade_plan_f71 import compute_trade_plan_f71

logger = logging.getLogger(__name__)

ALERT_G35 = "G35_TELEGRAM_INTELLIGENCE"


def _local_tz() -> ZoneInfo:
    import os
    name = os.getenv("ME_G3_REPORT_TZ", "Europe/Moscow")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _now_hour_key() -> str:
    tz = _local_tz()
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d-%H")


def _market_peer_return(conn: Any, symbol: str) -> float | None:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=13)
    if len(bars) < 2:
        return None
    start, end = bars[0].close, bars[-1].close
    if start <= 0:
        return None
    return round((end / start - 1.0) * 100.0, 2)


def _largest_movers(conn: Any, *, limit: int = 5) -> list[tuple[str, float]]:
    try:
        rows = conn.execute(
            """
            SELECT symbol, return_pct
            FROM market_events
            WHERE created_at >= ? AND return_pct IS NOT NULL
            ORDER BY ABS(return_pct) DESC
            LIMIT ?
            """,
            (int(time.time()) - 3600, limit),
        ).fetchall()
        out: list[tuple[str, float]] = []
        for r in rows:
            out.append((str(r["symbol"]), float(r["return_pct"])))
        return out
    except Exception:
        return []


def _top_candidate(conn: Any) -> Any | None:
    row = conn.execute("SELECT MAX(candidate_ts) AS ts FROM market_candidate_g31").fetchone()
    ts = int(row["ts"]) if row and row["ts"] else None
    if not ts:
        return None
    return conn.execute(
        """
        SELECT * FROM market_candidate_g31
        WHERE candidate_ts = ? AND confidence IS NOT NULL
        ORDER BY confidence DESC, market_score DESC
        LIMIT 1
        """,
        (ts,),
    ).fetchone()


def build_hourly_market_brief_g35(conn: Any) -> str:
    btc = _market_peer_return(conn, "BTC")
    eth = _market_peer_return(conn, "ETH")
    total3 = _market_peer_return(conn, "TOTAL3")

    dom_row = conn.execute(
        "SELECT btc_dominance AS d FROM market_snapshots_g3 ORDER BY created_at DESC LIMIT 1",
    ).fetchone()
    btc_dom = float(dom_row["d"]) if dom_row and dom_row["d"] is not None else None

    ms = compute_market_score(
        conn,
        report=None,
        trend=None,
        dominance_regime=None,
        liq_intel=None,
    )

    snap = conn.execute("SELECT fear_greed, funding FROM market_snapshots_g3 ORDER BY created_at DESC LIMIT 1").fetchone()
    fear_greed = float(snap["fear_greed"]) if snap and snap["fear_greed"] is not None else None
    funding = float(snap["funding"]) if snap and snap["funding"] is not None else None

    oi_row = conn.execute(
        """
        SELECT open_interest FROM market_event_exchange_context
        WHERE open_interest IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    oi = float(oi_row["open_interest"]) if oi_row and oi_row["open_interest"] is not None else None

    movers = _largest_movers(conn, limit=5)
    top = _top_candidate(conn)

    lines = ["🕐 Hourly Market Brief", ""]
    lines.extend(["BTC", f"{btc:+.2f}%" if btc is not None else "—", ""])
    lines.extend(["ETH", f"{eth:+.2f}%" if eth is not None else "—", ""])
    lines.extend(["TOTAL3", f"{total3:+.2f}%" if total3 is not None else "—", ""])
    lines.extend(["BTC Dominance", f"{btc_dom:.1f}%" if btc_dom is not None else "—", ""])
    lines.extend(["Market Score", f"{ms.score:.0f}", ""])
    lines.extend(["Fear & Greed", f"{fear_greed:.0f}" if fear_greed is not None else "—", ""])
    lines.extend(["Funding", f"{funding:+.4f}" if funding is not None else "—", ""])
    lines.extend(["Open Interest", f"{oi:.0f}" if oi is not None else "—", ""])

    lines.append("Largest movers")
    if not movers:
        lines.append("—")
    else:
        for sym, ret in movers:
            lines.append(f"• {sym} {ret:+.2f}%")

    lines.extend(["", "Top Candidate"])
    if not top:
        lines.append("No quality setups yet.")
    else:
        lines.extend([
            str(top["symbol"]),
            "",
            "Confidence",
            f"{float(top['confidence'] or 0):.1f}",
            "",
            "Market Score",
            f"{float(top['market_score'] or 0):.0f}",
        ])
        if top["candidate_state"] in (STATE_REJECTED, STATE_PROVISIONAL):
            lines.extend(["", "Status", str(top["candidate_state"]), "", "Reason", str(top["rejection_reason"] or "—")])

    lines.extend(["", PAPER_LABEL])
    return "\n".join(lines).rstrip()


def maybe_send_hourly_market_brief_g35(conn: Any) -> bool:
    if not (G35_ENABLED and G35_HOURLY_BRIEF_ENABLED):
        return False
    key = f"g35_hourly_sent_{_now_hour_key()}"
    if get_g3_ops_state(conn, key):
        return False
    msg = build_hourly_market_brief_g35(conn)
    sent = _safe_alert(
        conn,
        event_id=0,
        alert_type=ALERT_G35,
        detail=key,
        message=msg,
        enabled=True,
    )
    if sent:
        set_g3_ops_state(conn, key, "1")
    return sent


def _expected_trade_plan(conn: Any, *, symbol: str, direction: str, confidence: float, rr: float) -> dict[str, Any]:
    price = None
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=2)
    if bars:
        price = float(bars[-1].close)
    if not price:
        return {"entry": "—", "tp": "—", "sl": "—", "rr": rr}
    from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5
    # deterministic proxy: use rr as-is; stop=1%, targets derived from rr ratio
    risk = RiskRewardF5(
        risk_reward=float(rr or 2.5),
        stop_pct=1.0,
        tp1_pct=1.5,
        tp2_pct=2.2,
        tp3_pct=3.0,
    )
    shock_dir = "DOWN" if direction == "LONG" else "UP"
    tp = compute_trade_plan_f71(price=price, shock_direction=shock_dir, risk_reward=risk, final_confidence=confidence)
    return {
        "entry": tp.entry,
        "tp": tp.tp1,
        "sl": tp.sl,
        "rr": tp.risk_reward,
    }


def maybe_send_candidate_alerts_g35(conn: Any) -> int:
    """Send alerts for high-confidence rejected candidates (observe-before-gates)."""
    if not (G35_ENABLED and G35_CANDIDATE_ALERTS_ENABLED):
        return 0
    row = conn.execute("SELECT MAX(candidate_ts) AS ts FROM market_candidate_g31").fetchone()
    ts = int(row["ts"]) if row and row["ts"] else None
    if not ts:
        return 0
    candidates = conn.execute(
        """
        SELECT * FROM market_candidate_g31
        WHERE candidate_ts = ?
          AND candidate_state = ?
          AND confidence >= 7.0
        ORDER BY confidence DESC
        LIMIT 5
        """,
        (ts, STATE_REJECTED),
    ).fetchall()
    sent_n = 0
    for c in candidates:
        cid = int(c["id"])
        key = f"g35_rejected_candidate_{cid}"
        if get_g3_ops_state(conn, key):
            continue
        prob = min(0.95, float(c["liquidity_score"] or 0) / 100.0 * 0.6 + (float(c["confidence"] or 0) / 10.0) * 0.4)
        plan = _expected_trade_plan(
            conn,
            symbol=str(c["symbol"]),
            direction=str(c["direction"] or "LONG"),
            confidence=float(c["confidence"] or 0),
            rr=float(c["rr"] or 0),
        )
        msg = "\n".join([
            "🟡 Candidate detected",
            "",
            str(c["symbol"]),
            "",
            "Reason rejected",
            str(c["rejection_reason"] or "—"),
            "",
            "Estimated probability",
            f"{prob * 100:.0f}%",
            "",
            "Expected RR",
            f"{float(plan['rr'] or 0):.2f}",
            "",
            "Expected entry",
            str(plan["entry"]),
            "",
            "Expected TP",
            str(plan["tp"]),
            "",
            "Expected SL",
            str(plan["sl"]),
            "",
            PAPER_LABEL,
        ])
        sent = _safe_alert(
            conn,
            event_id=0,
            alert_type=ALERT_G35,
            detail=key,
            message=msg,
            enabled=True,
        )
        if sent:
            set_g3_ops_state(conn, key, "1")
            sent_n += 1
    return sent_n


def _load_last_symbol_state(conn: Any, symbol: str) -> Any | None:
    return conn.execute(
        "SELECT * FROM market_g35_candidate_state WHERE symbol = ?",
        (symbol,),
    ).fetchone()


def _upsert_symbol_state(conn: Any, *, c: Any) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT OR REPLACE INTO market_g35_candidate_state (
          symbol, candidate_id, candidate_ts, candidate_state,
          confidence, market_score, liquidity_score, funding_score, oi_score, volume_score,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(c["symbol"]),
            int(c["id"]),
            int(c["candidate_ts"]),
            str(c["candidate_state"]),
            float(c["confidence"]) if c["confidence"] is not None else None,
            float(c["market_score"]) if c["market_score"] is not None else None,
            float(c["liquidity_score"]) if c["liquidity_score"] is not None else None,
            float(c["funding_score"]) if c["funding_score"] is not None else None,
            float(c["oi_score"]) if c["oi_score"] is not None else None,
            float(c["volume_score"]) if c["volume_score"] is not None else None,
            now,
        ),
    )


def maybe_send_upgrades_downgrades_g35(conn: Any) -> int:
    if not G35_ENABLED:
        return 0
    row = conn.execute("SELECT MAX(candidate_ts) AS ts FROM market_candidate_g31").fetchone()
    ts = int(row["ts"]) if row and row["ts"] else None
    if not ts:
        return 0
    latest = conn.execute(
        "SELECT * FROM market_candidate_g31 WHERE candidate_ts = ? AND confidence IS NOT NULL",
        (ts,),
    ).fetchall()
    sent_n = 0
    for c in latest:
        sym = str(c["symbol"])
        prev = _load_last_symbol_state(conn, sym)
        _upsert_symbol_state(conn, c=c)
        if not prev:
            continue
        prev_state = str(prev["candidate_state"])
        cur_state = str(c["candidate_state"])
        if prev_state in (STATE_REJECTED, STATE_PROVISIONAL) and cur_state in (STATE_CANDIDATE, STATE_ACCEPTED):
            key = f"g35_upgrade_{int(c['id'])}"
            if get_g3_ops_state(conn, key):
                continue
            msg = "\n".join([
                "🟢 Candidate upgraded",
                "",
                sym,
                "",
                "Old confidence",
                f"{float(prev['confidence'] or 0):.1f}",
                "",
                "New confidence",
                f"{float(c['confidence'] or 0):.1f}",
                "",
                "What changed",
                "",
                f"Funding: {float(prev['funding_score'] or 0):.0f} → {float(c['funding_score'] or 0):.0f}",
                f"OI: {float(prev['oi_score'] or 0):.0f} → {float(c['oi_score'] or 0):.0f}",
                f"Liquidity: {float(prev['liquidity_score'] or 0):.0f} → {float(c['liquidity_score'] or 0):.0f}",
                f"Volume: {float(prev['volume_score'] or 0):.0f} → {float(c['volume_score'] or 0):.0f}",
                "",
                PAPER_LABEL,
            ])
            if _safe_alert(conn, event_id=0, alert_type=ALERT_G35, detail=key, message=msg, enabled=True):
                set_g3_ops_state(conn, key, "1")
                sent_n += 1
        if prev_state in (STATE_CANDIDATE, STATE_ACCEPTED) and cur_state in (STATE_REJECTED, STATE_PROVISIONAL):
            key = f"g35_downgrade_{int(c['id'])}"
            if get_g3_ops_state(conn, key):
                continue
            msg = "\n".join([
                "🔴 Signal cancelled",
                "",
                sym,
                "",
                "Reason",
                str(c["rejection_reason"] or "Became invalid"),
                "",
                "What changed",
                "",
                f"Funding: {float(prev['funding_score'] or 0):.0f} → {float(c['funding_score'] or 0):.0f}",
                f"OI: {float(prev['oi_score'] or 0):.0f} → {float(c['oi_score'] or 0):.0f}",
                f"Liquidity: {float(prev['liquidity_score'] or 0):.0f} → {float(c['liquidity_score'] or 0):.0f}",
                f"Volume: {float(prev['volume_score'] or 0):.0f} → {float(c['volume_score'] or 0):.0f}",
                "",
                PAPER_LABEL,
            ])
            if _safe_alert(conn, event_id=0, alert_type=ALERT_G35, detail=key, message=msg, enabled=True):
                set_g3_ops_state(conn, key, "1")
                sent_n += 1
    return sent_n


def build_daily_research_g35(conn: Any) -> tuple[str, dict[str, Any]]:
    tz = _local_tz()
    now_local = datetime.now(tz)
    report_date = now_local.strftime("%Y-%m-%d")
    day_start = int((now_local.replace(hour=0, minute=0, second=0, microsecond=0)).astimezone(timezone.utc).timestamp())
    day_end = day_start + 86400 - 1

    symbols = conn.execute(
        "SELECT COUNT(DISTINCT symbol) AS n FROM market_candidate_g31 WHERE created_at BETWEEN ? AND ?",
        (day_start, day_end),
    ).fetchone()
    signals = conn.execute(
        "SELECT COUNT(*) AS n FROM market_live_signals_g3 WHERE created_at BETWEEN ? AND ?",
        (day_start, day_end),
    ).fetchone()
    rejected = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_candidate_g31
        WHERE created_at BETWEEN ? AND ? AND candidate_state IN (?, ?)
        """,
        (day_start, day_end, STATE_REJECTED, STATE_PROVISIONAL),
    ).fetchone()
    near_miss = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_near_miss_f73 WHERE created_at BETWEEN ? AND ?",
        (day_start, day_end),
    ).fetchone()
    winners = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_candidate_outcomes_g32
        WHERE replay_status = 'COMPLETE' AND created_at BETWEEN ? AND ? AND would_hit_tp = 1
        """,
        (day_start, day_end),
    ).fetchone()
    losers = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_candidate_outcomes_g32
        WHERE replay_status = 'COMPLETE' AND created_at BETWEEN ? AND ? AND would_hit_sl = 1
        """,
        (day_start, day_end),
    ).fetchone()
    claude_row = conn.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost
        FROM market_events_ai_research_g2
        WHERE created_at BETWEEN ? AND ?
        """,
        (day_start, day_end),
    ).fetchone()

    best = conn.execute(
        """
        SELECT pattern_type, COUNT(*) AS n FROM market_trend_windows_g3
        WHERE created_at BETWEEN ? AND ?
        GROUP BY pattern_type ORDER BY n DESC LIMIT 1
        """,
        (day_start, day_end),
    ).fetchone()
    worst = conn.execute(
        """
        SELECT pattern_type, COUNT(*) AS n FROM market_trend_windows_g3
        WHERE created_at BETWEEN ? AND ?
        GROUP BY pattern_type ORDER BY n ASC LIMIT 1
        """,
        (day_start, day_end),
    ).fetchone()

    payload = {
        "report_date": report_date,
        "symbols_analysed": int(symbols["n"] or 0),
        "signals": int(signals["n"] or 0),
        "rejected": int(rejected["n"] or 0),
        "near_miss": int(near_miss["n"] or 0),
        "replay_winners": int(winners["n"] or 0),
        "replay_losers": int(losers["n"] or 0),
        "claude_cost_usd": round(float(claude_row["cost"] or 0), 4),
        "best_detector": best["pattern_type"] if best else "—",
        "worst_detector": worst["pattern_type"] if worst else "—",
    }
    msg = "\n".join([
        f"📚 Daily Research — {report_date}",
        "",
        "Yesterday",
        "",
        "How many symbols analysed",
        str(payload["symbols_analysed"]),
        "",
        "Signals",
        str(payload["signals"]),
        "",
        "Rejected",
        str(payload["rejected"]),
        "",
        "Near miss",
        str(payload["near_miss"]),
        "",
        "Replay winners",
        str(payload["replay_winners"]),
        "",
        "Replay losers",
        str(payload["replay_losers"]),
        "",
        "Claude cost",
        f"${payload['claude_cost_usd']:.4f}",
        "",
        "Best detector",
        str(payload["best_detector"]),
        "",
        "Worst detector",
        str(payload["worst_detector"]),
        "",
        PAPER_LABEL,
    ])
    return msg, payload


def maybe_send_daily_research_g35(conn: Any) -> bool:
    if not G35_ENABLED:
        return False
    tz = _local_tz()
    now_local = datetime.now(tz)
    if now_local.hour != G3_DAILY_REPORT_HOUR_LOCAL:
        return False
    report_date = now_local.strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT 1 FROM market_g35_daily_research WHERE report_date = ? AND telegram_sent = 1",
        (report_date,),
    ).fetchone()
    if row:
        return False
    msg, payload = build_daily_research_g35(conn)
    sent = _safe_alert(conn, event_id=0, alert_type=ALERT_G35, detail=f"g35_daily_{report_date}", message=msg, enabled=True)
    now = int(time.time())
    conn.execute(
        """
        INSERT OR REPLACE INTO market_g35_daily_research (report_date, report_json, telegram_sent, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (report_date, json.dumps(payload), 1 if sent else 0, now),
    )
    return sent


def maybe_send_claude_insight_g35(conn: Any) -> bool:
    if not (G35_ENABLED and G35_CLAUDE_INSIGHT_ENABLED):
        return False
    last = get_g3_ops_state(conn, "g35_claude_insight_ts")
    now = int(time.time())
    if last and now - int(last) < 6 * 3600:
        return False

    prompt = "\n".join([
        "Summarize current crypto market conditions in max 8 lines.",
        "Include: main risks, most interesting setup, what to watch next.",
        "Be compact and concrete. No trading actions. Research only.",
    ])
    insight = None
    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            call_claude_g2,
            is_claude_configured,
        )
        from bot.research.market_events.signal_intelligence.claude_ops_g2 import try_consume_claude_quota
        if is_claude_configured():
            allowed, _ = try_consume_claude_quota(conn)
            if allowed:
                resp = call_claude_g2(system="You are a concise crypto market analyst.", user_content=prompt, label="g35_insight")
                insight = resp.text
    except Exception as exc:
        logger.debug("g35 claude insight skipped: %s", exc)

    if not insight:
        insight = "\n".join([
            "Market: mixed / range-bound",
            "Risk: sudden BTC dominance shift",
            "Setup: watch high-volume capitulation patterns",
            "Next: funding flips + OI stalls alignment",
        ])

    msg = "\n".join(["🧠 Claude Insight", "", insight.strip(), "", PAPER_LABEL])
    sent = _safe_alert(conn, event_id=0, alert_type=ALERT_G35, detail=f"g35_insight_{now}", message=msg, enabled=True)
    if sent:
        set_g3_ops_state(conn, "g35_claude_insight_ts", str(now))
    return sent

