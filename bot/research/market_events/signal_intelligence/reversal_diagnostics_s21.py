"""Phase S2.1 — Reversal Diagnostics (read-only research report).

Last N candidates → Rejected / No reversal confirmation breakdown, then
counterfactual filter ablations (measure usefulness, do not change gates).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    load_recent_candles,
)
from bot.research.market_events.signal_intelligence.config import (
    G3_MIN_CONFIDENCE,
    G3_MIN_LIQUIDITY_PROB,
    G3_MIN_MARKET_SCORE,
    G3_MIN_RISK_REWARD,
)

DEFAULT_LIMIT = 500
REJECTION_NO_REVERSAL = "No reversal confirmation"

# Diagnostic checklist shown under each symbol (research-only; not production gates).
DIAG_FACTORS: tuple[str, ...] = (
    "Trend slope",
    "MACD",
    "EMA",
    "RSI",
    "Volume",
)

# Production G31 filters measured in the counterfactual section.
RULE_REVERSAL = "No reversal confirmation"
RULE_VOLUME = "Volume weak"
RULE_CONFIDENCE = "Confidence"
RULE_MARKET_SCORE = "Market Score"
RULE_LIQUIDITY = "Liquidity"
RULE_RR = "RR"
RULE_BTC = "BTC against trend"
RULE_FUNDING = "Funding unavailable"

RULE_ORDER: tuple[str, ...] = (
    RULE_FUNDING,
    RULE_VOLUME,
    RULE_REVERSAL,
    RULE_CONFIDENCE,
    RULE_MARKET_SCORE,
    RULE_LIQUIDITY,
    RULE_RR,
    RULE_BTC,
)


@dataclass(frozen=True)
class FactorStatus:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class AblationResult:
    rule: str
    extra_signals: int
    wins: int
    sample: int
    wr_pct: float | None
    note: str = ""


def _ema(values: list[float], period: int) -> list[float | None]:
    if period <= 0 or not values:
        return []
    k = 2.0 / (period + 1)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _slope_pct(closes: list[float], lookback: int = 8) -> float | None:
    if len(closes) < lookback:
        return None
    window = closes[-lookback:]
    first = window[0]
    if first <= 0:
        return None
    return (window[-1] / first - 1.0) * 100.0


def _macd_hist(closes: list[float]) -> float | None:
    if len(closes) < 35:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line: list[float] = []
    for a, b in zip(ema12, ema26):
        if a is None or b is None:
            macd_line.append(float("nan"))
        else:
            macd_line.append(a - b)
    valid = [x for x in macd_line if not math.isnan(x)]
    if len(valid) < 9:
        return None
    # rebuild signal on non-nan suffix
    clean = valid
    signal = _ema(clean, 9)
    if not signal or signal[-1] is None:
        return None
    return clean[-1] - float(signal[-1])


def analyze_confirmation_factors(
    bars: list[CandleBar],
    *,
    direction: str | None,
    volume_score: float | None,
) -> list[FactorStatus]:
    """Research diagnostics: which confirmation-style signals were weak."""
    closes = [float(b.close) for b in bars]
    vols = [float(b.volume) for b in bars]
    is_long = (direction or "LONG").upper() == "LONG"

    out: list[FactorStatus] = []

    slope = _slope_pct(closes)
    if slope is None:
        out.append(FactorStatus("Trend slope", False, "insufficient candles"))
    else:
        ok = slope > 0.15 if is_long else slope < -0.15
        out.append(FactorStatus("Trend slope", ok, f"{slope:+.2f}%"))

    hist = _macd_hist(closes)
    if hist is None:
        out.append(FactorStatus("MACD", False, "insufficient candles"))
    else:
        ok = hist > 0 if is_long else hist < 0
        out.append(FactorStatus("MACD", ok, f"hist {hist:+.4f}"))

    ema20 = _ema(closes, 20)
    if not ema20 or ema20[-1] is None:
        out.append(FactorStatus("EMA", False, "insufficient candles"))
    else:
        px = closes[-1]
        ema = float(ema20[-1])
        dist = (px / ema - 1.0) * 100.0 if ema else 0.0
        ok = px >= ema if is_long else px <= ema
        out.append(FactorStatus("EMA", ok, f"price vs EMA20 {dist:+.2f}%"))

    rsi = _rsi(closes)
    if rsi is None:
        out.append(FactorStatus("RSI", False, "insufficient candles"))
    else:
        if is_long:
            ok = 45.0 <= rsi <= 78.0
        else:
            ok = 22.0 <= rsi <= 55.0
        out.append(FactorStatus("RSI", ok, f"{rsi:.1f}"))

    if volume_score is not None:
        ok = float(volume_score) >= 40.0
        out.append(FactorStatus("Volume", ok, f"score {float(volume_score):.0f}"))
    elif len(vols) >= 10:
        recent = sum(vols[-5:])
        prior = sum(vols[-10:-5]) or 1.0
        ratio = recent / prior
        ok = ratio >= 1.0
        out.append(FactorStatus("Volume", ok, f"ratio {ratio:.2f}x"))
    else:
        out.append(FactorStatus("Volume", False, "unavailable"))

    return out


def _load_last_candidates(conn: Any, *, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id, c.symbol, c.candidate_ts, c.created_at, c.direction,
               c.candidate_state, c.rejection_reason,
               c.trend_score, c.market_score, c.liquidity_score, c.confidence, c.rr,
               c.btc_alignment, c.funding_score, c.volume_score,
               o.would_hit_tp, o.max_profit_pct, o.replay_status, o.price_entry
        FROM market_candidate_g31 c
        LEFT JOIN market_candidate_outcomes_g32 o ON o.candidate_id = c.id
        ORDER BY c.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _is_win(row: dict[str, Any]) -> bool | None:
    if row.get("would_hit_tp") is None and row.get("max_profit_pct") is None:
        return None
    if row.get("would_hit_tp"):
        return True
    return float(row.get("max_profit_pct") or 0) > 0


def _infer_reversal_pass(row: dict[str, Any]) -> bool | None:
    """True/False when knowable from first-fail encoding; None if blocked earlier."""
    reason = str(row.get("rejection_reason") or "")
    state = str(row.get("candidate_state") or "")
    if reason == REJECTION_NO_REVERSAL:
        return False
    if state in ("candidate", "accepted"):
        return True
    # Passed funding+volume; failed later numeric gate → reversal passed
    later = (
        reason.startswith("Confidence")
        or reason.startswith("Market Score")
        or reason.startswith("Liquidity")
        or reason.startswith("RR ")
        or reason == RULE_BTC
        or reason.startswith("Waiting")
        or reason.startswith("Confidence provisional")
    )
    if later:
        return True
    if reason == RULE_FUNDING or reason.startswith("Volume weak"):
        return None
    if not reason:
        return True
    return None


def _gate_results(row: dict[str, Any]) -> dict[str, bool | None]:
    funding_ok: bool | None
    if row.get("funding_score") is None:
        funding_ok = False
    else:
        funding_ok = True

    vol = row.get("volume_score")
    volume_ok: bool | None = None if vol is None else float(vol) >= 40.0

    conf = row.get("confidence")
    conf_ok: bool | None = None if conf is None else float(conf) >= G3_MIN_CONFIDENCE

    ms = row.get("market_score")
    ms_ok: bool | None = None if ms is None else float(ms) >= G3_MIN_MARKET_SCORE

    liq = row.get("liquidity_score")
    liq_ok: bool | None = (
        None if liq is None else float(liq) >= G3_MIN_LIQUIDITY_PROB * 100.0
    )

    rr = row.get("rr")
    rr_ok: bool | None = None if rr is None else float(rr) >= G3_MIN_RISK_REWARD

    btc = str(row.get("btc_alignment") or "")
    btc_ok: bool | None = None if not btc else btc != "Against"

    return {
        RULE_FUNDING: funding_ok,
        RULE_VOLUME: volume_ok,
        RULE_REVERSAL: _infer_reversal_pass(row),
        RULE_CONFIDENCE: conf_ok,
        RULE_MARKET_SCORE: ms_ok,
        RULE_LIQUIDITY: liq_ok,
        RULE_RR: rr_ok,
        RULE_BTC: btc_ok,
    }


def _passes_all_except(
    gates: dict[str, bool | None],
    *,
    skip: frozenset[str],
) -> bool:
    """True if no *known* failure outside skip.

    Unknown gates (None) — e.g. Reversal never evaluated because Volume
    failed first — do not block the counterfactual unlock count.
    """
    for name, ok in gates.items():
        if name in skip:
            continue
        if ok is False:
            return False
    return True


def _fails_any_of(gates: dict[str, bool | None], rules: frozenset[str]) -> bool:
    for name in rules:
        ok = gates.get(name)
        if ok is False:
            return True
    return False


def ablate_rules(
    rows: list[dict[str, Any]],
    *,
    skip_rules: frozenset[str],
) -> AblationResult:
    """Candidates that fail ≥1 skipped rule but pass every other rule → extra signals."""
    label = " + ".join(sorted(skip_rules)) if len(skip_rules) > 1 else next(iter(skip_rules))
    extras: list[dict[str, Any]] = []
    for row in rows:
        gates = _gate_results(row)
        if not _fails_any_of(gates, skip_rules):
            continue
        if not _passes_all_except(gates, skip=skip_rules):
            continue
        extras.append(row)

    scored = [r for r in extras if _is_win(r) is not None]
    wins = sum(1 for r in scored if _is_win(r))
    n = len(scored)
    wr = round(100.0 * wins / n, 1) if n else None
    note = ""
    if extras and not scored:
        note = "no replay outcomes yet"
    return AblationResult(
        rule=label,
        extra_signals=len(extras),
        wins=wins,
        sample=n,
        wr_pct=wr,
        note=note,
    )


def collect_reversal_rejects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r for r in rows
        if str(r.get("candidate_state")) == "rejected"
        and str(r.get("rejection_reason") or "") == REJECTION_NO_REVERSAL
    ]


def symbol_missing_factors(
    conn: Any,
    rejects: list[dict[str, Any]],
) -> dict[str, list[FactorStatus]]:
    """Per symbol: factors that failed on the newest rejection (representative)."""
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rejects:
        by_sym[str(r["symbol"]).upper()].append(r)

    result: dict[str, list[FactorStatus]] = {}
    for sym, items in sorted(by_sym.items()):
        newest = max(items, key=lambda x: int(x.get("id") or 0))
        bars = load_recent_candles(
            conn, symbol=sym, venue="binance_futures", timeframe="5m", limit=80,
        )
        factors = analyze_confirmation_factors(
            bars,
            direction=newest.get("direction"),
            volume_score=newest.get("volume_score"),
        )
        # Keep only what was missing (failed)
        missing = [f for f in factors if not f.ok]
        if not missing:
            missing = [
                FactorStatus(
                    "—",
                    False,
                    "all diagnostic checks soft-pass; confirmation key/rate may still fail",
                )
            ]
        result[sym] = missing
    return result


def build_reversal_diagnostics_s21(
    conn: Any,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    rows = _load_last_candidates(conn, limit=limit)
    rejects = collect_reversal_rejects(rows)
    missing = symbol_missing_factors(conn, rejects)

    single = [ablate_rules(rows, skip_rules=frozenset({rule})) for rule in RULE_ORDER]
    single = [a for a in single if a.extra_signals > 0]

    # Pairwise for the two most common blocking rules that unlock signals
    pairs: list[AblationResult] = []
    top = sorted(single, key=lambda a: a.extra_signals, reverse=True)[:4]
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            pairs.append(
                ablate_rules(
                    rows,
                    skip_rules=frozenset({top[i].rule, top[j].rule}),
                )
            )
    pairs = [p for p in pairs if p.extra_signals > 0]
    pairs.sort(key=lambda a: a.extra_signals, reverse=True)

    return {
        "limit": limit,
        "total_candidates": len(rows),
        "reversal_rejects": len(rejects),
        "missing_by_symbol": missing,
        "single_ablations": single,
        "pair_ablations": pairs[:6],
    }


def format_reversal_diagnostics_s21(
    conn: Any,
    *,
    limit: int = DEFAULT_LIMIT,
) -> str:
    data = build_reversal_diagnostics_s21(conn, limit=limit)
    lines = [
        "Rejected",
        "",
        "No reversal confirmation",
        "",
        "==================",
        "",
    ]

    missing: dict[str, list[FactorStatus]] = data["missing_by_symbol"]
    if not missing:
        lines.extend([
            f"(none in last {data['limit']} candidates — "
            f"{data['reversal_rejects']} reversal rejects)",
            "",
        ])
    else:
        syms = list(missing.keys())
        for i, sym in enumerate(syms):
            lines.append(sym)
            lines.append("")
            lines.append("что не хватило")
            lines.append("")
            for f in missing[sym]:
                if f.name == "—":
                    lines.append(f.detail)
                else:
                    lines.append(f.name)
                    if f.detail:
                        lines.append(f"  ({f.detail})")
            lines.append("")
            if i < len(syms) - 1:
                lines.append("------------------")
                lines.append("")

    lines.extend([
        "===================",
        "",
        "Filter usefulness (counterfactual)",
        f"Window: last {data['limit']} candidates "
        f"({data['reversal_rejects']} No reversal confirmation rejects)",
        "",
    ])

    singles: list[AblationResult] = data["single_ablations"]
    if not singles:
        lines.append("No single-rule ablations unlocked extra signals in this window.")
    else:
        for a in singles:
            lines.append(f"Если убрать только правило {a.rule}")
            lines.append("")
            lines.append("получим")
            lines.append("")
            lines.append(f"+{a.extra_signals} сигналов")
            lines.append("")
            lines.append("исторический WR")
            lines.append("")
            if a.wr_pct is not None:
                lines.append(f"{a.wr_pct:.0f}%")
                lines.append(f"(n={a.sample}, wins={a.wins})")
            else:
                lines.append("n/a")
                if a.note:
                    lines.append(f"({a.note})")
            lines.append("")
            lines.append("===================")
            lines.append("")

    pairs: list[AblationResult] = data["pair_ablations"]
    for a in pairs[:3]:
        lines.append(f"Если убрать оба ({a.rule})")
        lines.append("")
        lines.append("получим")
        lines.append("")
        lines.append(f"+{a.extra_signals} сигналов")
        lines.append("")
        lines.append("WR")
        lines.append("")
        if a.wr_pct is not None:
            lines.append(f"{a.wr_pct:.0f}%")
            lines.append(f"(n={a.sample})")
        else:
            lines.append("n/a")
        lines.append("")
        lines.append("===================")
        lines.append("")

    lines.append(
        "Note: read-only research. Production thresholds / scoring unchanged."
    )
    return "\n".join(lines).rstrip() + "\n"
