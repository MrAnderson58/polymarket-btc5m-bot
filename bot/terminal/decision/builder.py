"""Build DecisionCard from Scanner + MarketProfile + Signal + Learning."""

from __future__ import annotations

from bot.terminal.decision.explain import (
    build_reasons,
    build_warnings,
    holding_time_for,
    template_ai_summary,
)
from bot.terminal.decision.models import DecisionCard, DecisionInputs, LearningHint
from bot.terminal.decision.scoring import (
    decision_score,
    display_confidence,
    reward_multiples,
    risk_budget_pct,
)
from bot.terminal.instruments.profiles import MarketProfile, get_market_profile
from bot.terminal.models.dto import SignalCard
from bot.terminal.scanner.models import ScannerResult


def _resolve_price(scan: ScannerResult, signal: SignalCard | None, price: float | None) -> float | None:
    if price is not None and price > 0:
        return float(price)
    for src in (scan.extra, signal.extra if signal else None):
        if not src:
            continue
        for key in ("price", "entry", "last", "mark"):
            raw = src.get(key)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if val > 0:
                return val
    return None


def _levels(
    *,
    direction: str,
    price: float | None,
    score: float,
    confidence: float | None,
) -> tuple[float | None, float | None, float | None, float | None, str]:
    """Illustrative entry/stop/tp from risk % — not live execution quotes."""
    risk_pct = risk_budget_pct(score, confidence)
    r1, r2 = reward_multiples(score)
    risk_label = f"{risk_pct:.1f}% notional · TP {r1:.1f}R / {r2:.1f}R"

    if price is None:
        return None, None, None, None, risk_label

    entry = round(price, 2)
    dist = entry * (risk_pct / 100.0)
    d = (direction or "").upper()
    if d == "SHORT":
        stop = round(entry + dist, 2)
        tp1 = round(entry - dist * r1, 2)
        tp2 = round(entry - dist * r2, 2)
    else:
        # LONG / unknown → long-style structure
        stop = round(entry - dist, 2)
        tp1 = round(entry + dist * r1, 2)
        tp2 = round(entry + dist * r2, 2)
    return entry, stop, tp1, tp2, risk_label


def build_decision_card(inputs: DecisionInputs) -> DecisionCard:
    """Compose a trader-facing DecisionCard (presentation layer only)."""
    scan = inputs.scan
    profile = inputs.profile
    if profile is None:
        try:
            profile = get_market_profile(scan.asset_class)
        except Exception:
            profile = None

    learning = inputs.learning
    signal = inputs.signal
    conf = display_confidence(scan)
    score = decision_score(scan, learning)
    direction = (scan.direction or (signal.direction if signal else "") or "—").upper()

    price = _resolve_price(scan, signal, inputs.price)
    entry, stop, tp1, tp2, risk = _levels(
        direction=direction, price=price, score=score, confidence=conf
    )
    reasons = build_reasons(scan, profile, learning, signal)
    warnings = build_warnings(scan, profile, learning)
    holding = holding_time_for(profile, score)
    summary = template_ai_summary(
        symbol=scan.symbol,
        direction=direction,
        score=score,
        confidence=conf,
        reasons=reasons,
        warnings=warnings,
        risk=risk,
        holding_time=holding,
    )

    return DecisionCard(
        symbol=scan.symbol.upper().replace("USDT", ""),
        direction=direction,
        confidence=conf,
        score=score,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        risk=risk,
        holding_time=holding,
        reasons=reasons,
        warnings=warnings,
        ai_summary=summary,
        asset_class=scan.asset_class,
        provider=scan.provider,
        extra={
            "source": "DecisionBuilder",
            "scan_score": scan.score,
            "has_price": price is not None,
        },
    )


def build_from_scan(
    scan: ScannerResult,
    *,
    profile: MarketProfile | None = None,
    signal: SignalCard | None = None,
    learning: LearningHint | None = None,
    price: float | None = None,
) -> DecisionCard:
    return build_decision_card(
        DecisionInputs(
            scan=scan,
            profile=profile,
            signal=signal,
            learning=learning,
            price=price,
        )
    )


__all__ = ["build_decision_card", "build_from_scan"]
