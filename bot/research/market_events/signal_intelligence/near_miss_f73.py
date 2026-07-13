"""Phase F.7.3 — near miss recording and rejection diagnostics."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import (
    F5_MIN_TELEGRAM_CONFIDENCE,
    F7_MIN_FINAL_CONFIDENCE,
    F7_MIN_MARKET_SCORE,
    F73_ENABLED,
)

_TABLE = "market_events_near_miss_f73"

CAT_CONFIDENCE = "Confidence"
CAT_MARKET_SCORE = "Market Score"
CAT_FUNDING = "Funding"
CAT_VOLUME = "Volume"
CAT_TREND = "Trend"
CAT_BTC = "BTC against trade"
CAT_CONFIRMATION = "No confirmation"
CAT_PRIORITY = "Priority"
CAT_OTHER = "Other"

_SKIP_CATEGORY = {
    "low_confidence": CAT_CONFIDENCE,
    "low_final_confidence": CAT_CONFIDENCE,
    "low_market_score": CAT_MARKET_SCORE,
    "not_top3": CAT_PRIORITY,
    "window_full": CAT_PRIORITY,
    "already_sent": CAT_PRIORITY,
    "send_failed": CAT_OTHER,
    "f5_filtered": CAT_OTHER,
}


def _primary_detector(conn: Any, event_id: int) -> str:
    row = conn.execute(
        "SELECT detector_triggers_json FROM market_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return "SHOCK_A"
    try:
        triggers = json.loads(row["detector_triggers_json"] or "[]")
        if triggers:
            return str(triggers[0])
    except (json.JSONDecodeError, TypeError):
        pass
    return "SHOCK_A"


def _trend_score(trend: dict[str, Any] | None) -> float | None:
    if not trend:
        return None
    raw = trend.get("trend_score")
    return float(raw) if raw is not None else None


def build_rejection_details(
    conn: Any,
    *,
    event_id: int,
    skip_code: str,
    signal: Any | None = None,
    f7_intel: Any | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (human_reason, category, missing_conditions_json)."""
    from bot.research.market_events.signal_intelligence.market_intel_f7 import (
        load_market_intelligence_f7,
    )
    from bot.research.market_events.signal_intelligence.professional_signal_f5 import (
        load_professional_signal_f5,
    )
    from bot.research.market_events.signal_intelligence.signal_report_f2 import (
        load_signal_report_f2,
    )
    from bot.research.market_events.signal_intelligence.trend_shock_v2 import load_trend_shock_v2

    signal = signal or load_professional_signal_f5(conn, event_id)
    f7_intel = f7_intel or load_market_intelligence_f7(conn, event_id)
    report = load_signal_report_f2(conn, event_id)
    trend = load_trend_shock_v2(conn, event_id)

    reasons: list[str] = []
    missing: dict[str, Any] = {"skip_code": skip_code}

    dyn_conf = float(signal.dynamic_confidence) if signal else 0.0
    final_conf = float(f7_intel.final_confidence) if f7_intel else dyn_conf
    market_score = float(f7_intel.market_score) if f7_intel else None

    if skip_code in ("low_confidence", "low_final_confidence") or final_conf < F7_MIN_FINAL_CONFIDENCE:
        threshold = F7_MIN_FINAL_CONFIDENCE if f7_intel else F5_MIN_TELEGRAM_CONFIDENCE
        conf_used = final_conf if f7_intel else dyn_conf
        if conf_used < threshold:
            reasons.append(f"Confidence {conf_used:.1f} < {threshold:.1f}")
            missing["confidence"] = {"actual": round(conf_used, 2), "required": threshold}

    if skip_code == "low_market_score" or (market_score is not None and market_score < F7_MIN_MARKET_SCORE):
        if market_score is not None and market_score < F7_MIN_MARKET_SCORE:
            reasons.append(f"Market Score {market_score:.0f} < {F7_MIN_MARKET_SCORE:.0f}")
            missing["market_score"] = {"actual": round(market_score, 1), "required": F7_MIN_MARKET_SCORE}

    funding = trend.get("funding") if trend else None
    funding_regime = report.funding_regime if report else None
    if funding is None and funding_regime in (None, "unknown", "unavailable"):
        reasons.append("Funding unavailable")
        missing["funding"] = {"available": False}
    elif funding_regime in ("extreme_long", "extreme_short") and skip_code != "low_market_score":
        missing["funding_regime"] = funding_regime

    trend_sc = _trend_score(trend)
    if trend_sc is not None and trend_sc < 50:
        reasons.append("Trend score too low")
        missing["trend_score"] = {"actual": round(trend_sc, 1), "required": 50}

    vol_mult = None
    if trend and trend.get("volume_multiple") is not None:
        vol_mult = float(trend["volume_multiple"])
    elif report:
        vol_mult = float(report.rvol_20 or 0)
    if vol_mult is not None and 0 < vol_mult < 1.5:
        reasons.append(f"Volume {vol_mult:.1f}×")
        missing["volume_multiple"] = {"actual": round(vol_mult, 2), "required": 1.5}

    if report and report.correlation_verdict in ("against", "btc_against", "BTC against"):
        reasons.append("BTC against trade")
        missing["btc_correlation"] = report.correlation_verdict

    if skip_code == "not_top3":
        reasons.append("Not in top 3 priority window")
    elif skip_code == "window_full":
        reasons.append("Telegram window full (3/3 sent)")
    elif skip_code == "already_sent":
        reasons.append("Already sent in window")
    elif skip_code == "send_failed":
        reasons.append("Telegram send failed")
    elif skip_code == "f5_filtered" and signal and signal.telegram_skip_reason:
        reasons.append(f"F5 filtered: {signal.telegram_skip_reason}")

    if skip_code in ("not_top3", "window_full", "already_sent") and "No confirmation" not in reasons:
        reasons.append("No confirmation")

    category = _SKIP_CATEGORY.get(skip_code, CAT_OTHER)
    if not reasons:
        reasons.append(skip_code.replace("_", " ").capitalize())

    # Refine category from missing conditions only when skip code is generic
    if category == CAT_OTHER:
        if "confidence" in missing:
            category = CAT_CONFIDENCE
        elif "market_score" in missing:
            category = CAT_MARKET_SCORE
        elif "funding" in missing and missing["funding"].get("available") is False:
            category = CAT_FUNDING
        elif "trend_score" in missing:
            category = CAT_TREND
        elif "volume_multiple" in missing:
            category = CAT_VOLUME
        elif "btc_correlation" in missing:
            category = CAT_BTC

    return "; ".join(reasons), category, missing


def record_near_miss_f73(
    conn: Any,
    *,
    event_id: int,
    skip_code: str,
    signal: Any | None = None,
    f7_intel: Any | None = None,
) -> bool:
    """Persist rejected candidate. Returns True if inserted."""
    if not F73_ENABLED:
        return False

    existing = conn.execute(
        f"SELECT 1 FROM {_TABLE} WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if existing:
        return False

    row = conn.execute(
        "SELECT symbol, return_pct, event_ts FROM market_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return False

    from bot.research.market_events.signal_intelligence.market_intel_f7 import (
        load_market_intelligence_f7,
    )
    from bot.research.market_events.signal_intelligence.professional_signal_f5 import (
        load_professional_signal_f5,
    )
    from bot.research.market_events.signal_intelligence.trend_shock_v2 import load_trend_shock_v2

    signal = signal or load_professional_signal_f5(conn, event_id)
    if not signal:
        return False

    f7_intel = f7_intel or load_market_intelligence_f7(conn, event_id)
    trend = load_trend_shock_v2(conn, event_id)
    reason, category, missing = build_rejection_details(
        conn, event_id=event_id, skip_code=skip_code, signal=signal, f7_intel=f7_intel,
    )

    conf = float(f7_intel.final_confidence) if f7_intel else float(signal.dynamic_confidence)
    mscore = float(f7_intel.market_score) if f7_intel else None
    now = int(time.time())

    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TABLE} (
          event_id, symbol, event_ts, detector, return_pct, trend_score,
          market_score, confidence, rejection_reason, rejection_category,
          missing_conditions_json, skip_code, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            str(row["symbol"]),
            int(row["event_ts"]),
            _primary_detector(conn, event_id),
            float(row["return_pct"] or 0),
            _trend_score(trend),
            mscore,
            conf,
            reason,
            category,
            json.dumps(missing, ensure_ascii=False),
            skip_code,
            now,
        ),
    )
    return True
