"""Phase S2.2 — explain Decision / Reversal gates with literal PASS/FAIL conditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import CandleBar, load_recent_candles
from bot.research.market_events.signal_intelligence.reversal_diagnostics_s21 import (
    analyze_confirmation_factors,
)
from bot.research.market_events.signal_intelligence.reversal_learning_g1 import (
    lookup_historical_reversal_rate,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3

REVERSAL_RATE_THRESHOLD = 0.45
REVERSAL_STREAK_CANDLES = 8
REVERSAL_STREAK_SCORE = 55.0
REVERSAL_SPECIAL_PATTERNS = frozenset({
    "slow_bleed", "capitulation", "accumulation", "distribution",
})


@dataclass(frozen=True)
class ConditionS22:
    index: int
    name: str
    passed: bool
    value: str
    threshold: str
    because: str

    @property
    def label(self) -> str:
        return "PASS" if self.passed else "FAIL"


@dataclass(frozen=True)
class ReversalEvalS22:
    passed: bool
    key: str
    conditions: tuple[ConditionS22, ...]
    because: str


@dataclass(frozen=True)
class FactorExplainS22:
    name: str
    passed: bool
    value: str
    threshold: str
    rule: str
    because: str

    @property
    def label(self) -> str:
        return "PASS" if self.passed else "FAIL"


def evaluate_reversal_confirmation_s22(
    conn: Any,
    *,
    symbol: str,
    trend: TrendWindowG3,
) -> ReversalEvalS22:
    """Literal conditions behind G31 `No reversal confirmation` (OR of 3 rules)."""
    key = f"{symbol}|{trend.pattern_type}|{trend.window_minutes}m"
    rate = lookup_historical_reversal_rate(conn, key)
    rate_present = rate is not None
    rate_ok = bool(rate_present and float(rate) >= REVERSAL_RATE_THRESHOLD)
    consec = int(trend.consecutive_candles or 0)
    score = float(trend.trend_score or 0)
    consec_ok = consec >= REVERSAL_STREAK_CANDLES
    score_ok = score >= REVERSAL_STREAK_SCORE
    streak_ok = consec_ok and score_ok
    pattern = str(trend.pattern_type or "")
    pattern_ok = pattern in REVERSAL_SPECIAL_PATTERNS

    conditions = (
        ConditionS22(
            index=1,
            name="historical_reversal_rate present",
            passed=rate_present,
            value="None" if rate is None else f"{float(rate):.4f}",
            threshold="not None",
            because=(
                f"lookup({key}) returned a rate"
                if rate_present
                else f"lookup({key}) returned None — no learned rate for this pattern key"
            ),
        ),
        ConditionS22(
            index=2,
            name="historical_reversal_rate >= threshold",
            passed=rate_ok,
            value="n/a" if rate is None else f"{float(rate):.4f}",
            threshold=str(REVERSAL_RATE_THRESHOLD),
            because=(
                f"rate {float(rate):.4f} >= {REVERSAL_RATE_THRESHOLD}"
                if rate_ok
                else (
                    "rate missing — cannot satisfy rate rule"
                    if rate is None
                    else f"rate {float(rate):.4f} < {REVERSAL_RATE_THRESHOLD}"
                )
            ),
        ),
        ConditionS22(
            index=3,
            name="consecutive_candles >= streak",
            passed=consec_ok,
            value=str(consec),
            threshold=str(REVERSAL_STREAK_CANDLES),
            because=(
                f"consecutive_candles {consec} >= {REVERSAL_STREAK_CANDLES}"
                if consec_ok
                else f"consecutive_candles {consec} < {REVERSAL_STREAK_CANDLES}"
            ),
        ),
        ConditionS22(
            index=4,
            name="trend_score >= streak score",
            passed=score_ok,
            value=f"{score:.2f}",
            threshold=str(REVERSAL_STREAK_SCORE),
            because=(
                f"trend_score {score:.2f} >= {REVERSAL_STREAK_SCORE}"
                if score_ok
                else f"trend_score {score:.2f} < {REVERSAL_STREAK_SCORE}"
            ),
        ),
        ConditionS22(
            index=5,
            name="pattern_type in special set",
            passed=pattern_ok,
            value=pattern or "—",
            threshold=",".join(sorted(REVERSAL_SPECIAL_PATTERNS)),
            because=(
                f"pattern_type '{pattern}' is in special set"
                if pattern_ok
                else f"pattern_type '{pattern}' not in special set"
            ),
        ),
    )

    # Production OR: (rate present AND rate>=thr) OR (consec AND score) OR pattern
    rule_rate = rate_ok  # already includes present
    rule_streak = streak_ok
    rule_pattern = pattern_ok
    passed = bool(rule_rate or rule_streak or rule_pattern)

    if passed:
        winners = []
        if rule_rate:
            winners.append("rate rule (C1∧C2)")
        if rule_streak:
            winners.append("streak rule (C3∧C4)")
        if rule_pattern:
            winners.append("pattern rule (C5)")
        because = "TRUE because " + "; ".join(winners)
    else:
        because = (
            "FALSE because rate rule failed (C1∧C2), "
            "streak rule failed (C3∧C4), and pattern rule failed (C5)"
        )

    return ReversalEvalS22(passed=passed, key=key, conditions=conditions, because=because)


def format_reversal_conditions_s22(eval_: ReversalEvalS22) -> str:
    lines = [
        "No reversal confirmation",
        "",
        "TRUE" if eval_.passed else "FALSE",
        "",
        "потому что",
        "",
        eval_.because,
        "",
        f"key {eval_.key}",
        "",
        "Production rule:",
        "(C1 AND C2) OR (C3 AND C4) OR C5",
        "",
    ]
    for c in eval_.conditions:
        lines.extend([
            f"Condition {c.index} {c.label}",
            c.name,
            f"value {c.value}",
            f"threshold {c.threshold}",
            f"потому что {c.because}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _latest_trend_for_symbol(conn: Any, symbol: str) -> TrendWindowG3 | None:
    row = conn.execute(
        """
        SELECT symbol, direction, trend_score, rejection_reason, candidate_state,
               trend_windows_json
        FROM market_candidate_g31
        WHERE symbol = ?
        ORDER BY id DESC LIMIT 1
        """,
        (symbol.upper(),),
    ).fetchone()
    if not row:
        return None

    # Prefer live trend detection when possible
    try:
        from bot.research.market_events.signal_intelligence.trend_windows_g3 import (
            detect_trends_g3,
        )
        trends = detect_trends_g3(conn, symbol=symbol.upper())
        if trends:
            return max(trends, key=lambda t: t.trend_score)
    except Exception:
        pass

    direction = str(row["direction"] or "LONG")
    trend_dir = "UP" if direction == "LONG" else "DOWN"
    return TrendWindowG3(
        symbol=symbol.upper(),
        window_minutes=60,
        pattern_type="unknown",
        consecutive_candles=0,
        trend_score=float(row["trend_score"] or 0),
        direction=trend_dir,
        details={},
    )


def _funding_from_snapshot(conn: Any) -> float | None:
    try:
        row = conn.execute(
            """
            SELECT funding FROM market_snapshots_g3
            WHERE funding IS NOT NULL
            ORDER BY snapshot_ts DESC LIMIT 1
            """,
        ).fetchone()
        if row and row["funding"] is not None:
            return float(row["funding"])
    except Exception:
        return None
    return None


def build_explain_decision_s22(conn: Any, symbol: str) -> dict[str, Any]:
    """Read-only decision/gate explanation for a symbol."""
    sym = symbol.upper().replace("USDT", "").strip() or "BTC"
    from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
        fetch_symbol_market_data_g01,
    )

    data = fetch_symbol_market_data_g01(conn, sym, limit=60, persist_state=False)
    bars: list[CandleBar] = list(data.bars or [])
    if not bars:
        bars = load_recent_candles(
            conn, symbol=sym, venue="binance_futures", timeframe="5m", limit=80,
        )

    trend = _latest_trend_for_symbol(conn, sym)
    if trend is None:
        # Synthetic flat trend so reversal dump still prints all conditions
        trend = TrendWindowG3(
            symbol=sym,
            window_minutes=60,
            pattern_type="unknown",
            consecutive_candles=0,
            trend_score=0.0,
            direction="UP",
            details={},
        )

    direction = "LONG" if trend.direction == "UP" else "SHORT"
    diag = analyze_confirmation_factors(
        bars,
        direction=direction,
        volume_score=None,
    )
    by_name = {f.name: f for f in diag}

    funding = data.funding if data.funding is not None else _funding_from_snapshot(conn)
    # Funding PASS when present (G31: funding unavailable fails)
    funding_ok = funding is not None

    # Trend PASS when slope aligns with direction (same diagnostic rule, explicit numbers)
    slope_f = by_name.get("Trend slope")
    trend_factor = FactorExplainS22(
        name="Trend",
        passed=bool(slope_f and slope_f.ok),
        value=slope_f.detail if slope_f else "n/a",
        threshold="slope > +0.15% (LONG) or < -0.15% (SHORT)",
        rule="Trend slope aligned with candidate direction",
        because=(
            slope_f.detail if slope_f and slope_f.ok
            else (slope_f.detail if slope_f else "no candles")
        ),
    )
    funding_factor = FactorExplainS22(
        name="Funding",
        passed=funding_ok,
        value="None" if funding is None else f"{funding:.6f}",
        threshold="funding not None",
        rule="G31 Funding available",
        because=(
            f"funding={funding:.6f} present"
            if funding_ok
            else "funding unavailable"
        ),
    )

    def _from_diag(name: str, rule: str, threshold: str) -> FactorExplainS22:
        f = by_name.get(name)
        return FactorExplainS22(
            name=name,
            passed=bool(f and f.ok),
            value=f.detail if f else "n/a",
            threshold=threshold,
            rule=rule,
            because=(f.detail if f else "unavailable"),
        )

    factors = [
        trend_factor,
        funding_factor,
        _from_diag("MACD", "MACD histogram sign matches direction", "hist>0 LONG / hist<0 SHORT"),
        _from_diag("EMA", "Price vs EMA20 matches direction", "price≥EMA20 LONG / price≤EMA20 SHORT"),
        _from_diag("RSI", "RSI not extreme against direction", "LONG 45–78 / SHORT 22–55"),
    ]

    rev = evaluate_reversal_confirmation_s22(conn, symbol=sym, trend=trend)
    rev_factor = FactorExplainS22(
        name="Reversal",
        passed=rev.passed,
        value="TRUE" if rev.passed else "FALSE",
        threshold="(C1∧C2)∨(C3∧C4)∨C5",
        rule="G31 No reversal confirmation gate",
        because=rev.because,
    )

    latest = conn.execute(
        """
        SELECT candidate_state, rejection_reason, confidence, market_score, volume_score, rr
        FROM market_candidate_g31 WHERE symbol = ? ORDER BY id DESC LIMIT 1
        """,
        (sym,),
    ).fetchone()

    return {
        "symbol": sym,
        "direction": direction,
        "factors": factors,
        "reversal": rev_factor,
        "reversal_eval": rev,
        "candidate": dict(latest) if latest else None,
        "source": data.source,
    }


def format_explain_decision_s22(conn: Any, symbol: str) -> str:
    data = build_explain_decision_s22(conn, symbol)
    sym = data["symbol"]
    lines = [
        f"explain-decision {sym}",
        "",
        f"Direction {data['direction']}",
        f"Source {data.get('source') or '—'}",
        "",
    ]
    for f in data["factors"]:
        assert isinstance(f, FactorExplainS22)
        lines.extend([
            f.name,
            "",
            f.label,
            "",
        ])
        if not f.passed:
            lines.extend([
                "↓",
                "",
                "Почему FAIL",
                "",
                f"правило: {f.rule}",
                "",
                f"число: {f.value}",
                "",
                f"threshold: {f.threshold}",
                "",
                f"потому что: {f.because}",
                "",
            ])

    rev_f: FactorExplainS22 = data["reversal"]
    lines.extend([
        "Reversal",
        "",
        rev_f.label,
        "",
    ])
    if not rev_f.passed:
        lines.extend([
            "↓",
            "",
            "Почему FAIL",
            "",
            f"правило: {rev_f.rule}",
            "",
            f"число: {rev_f.value}",
            "",
            f"threshold: {rev_f.threshold}",
            "",
            f"потому что: {rev_f.because}",
            "",
        ])
    lines.append(format_reversal_conditions_s22(data["reversal_eval"]))

    cand = data.get("candidate")
    if cand:
        lines.extend([
            "Latest candidate",
            f"state {cand.get('candidate_state')}",
            f"reason {cand.get('rejection_reason') or '—'}",
            f"confidence {cand.get('confidence')}",
            f"market_score {cand.get('market_score')}",
            f"volume_score {cand.get('volume_score')}",
            f"rr {cand.get('rr')}",
            "",
        ])
    lines.append("READ ONLY — no INSERT.")
    return "\n".join(lines).rstrip() + "\n"
