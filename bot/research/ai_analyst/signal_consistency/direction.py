"""S51 — Machine direction scoring. LLM may explain, never flip the direction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DirectionDecision:
    direction: str  # LONG | SHORT | WAIT
    score: float  # -100..+100 (positive = long bias)
    confidence_raw: float  # 0..100 before calibration
    factors: list[str]

    @property
    def is_actionable(self) -> bool:
        return self.direction in ("LONG", "SHORT")


def score_direction_from_context(ctx: dict[str, Any]) -> DirectionDecision:
    """
    Deterministic long/short score from numeric + intel context.
    Positive score → LONG, negative → SHORT, near zero → WAIT.
    """
    score = 0.0
    factors: list[str] = []

    btc = ctx.get("btc") or {}
    change = btc.get("change_24h_pct")
    if change is not None:
        try:
            ch = float(change)
            if ch > 0.5:
                score += 15
                factors.append(f"BTC 24h +{ch:.2f}%")
            elif ch < -0.5:
                score -= 15
                factors.append(f"BTC 24h {ch:.2f}%")
        except (TypeError, ValueError):
            pass

    etf = ((ctx.get("etf") or {}).get("btc_etf") or {})
    for key, weight in (("netflow_1d", 25), ("netflow_5d", 20)):
        if etf.get(key) is None:
            continue
        try:
            nf = float(etf[key])
            label = "1d" if "1d" in key else "5d"
            if nf > 0:
                score += weight if nf >= 50 else weight * 0.5
                factors.append(f"ETF +{nf:.0f}M ({label})")
            elif nf < 0:
                score -= weight if abs(nf) >= 50 else weight * 0.5
                factors.append(f"ETF {nf:.0f}M ({label})")
        except (TypeError, ValueError):
            pass

    funding = ctx.get("funding") or {}
    if funding.get("current") is not None:
        try:
            cur = float(funding["current"])
            if abs(cur) < 0.00015:
                factors.append("Funding neutral")
            elif cur > 0.0003:
                score -= 12
                factors.append(f"Funding elevated ({cur})")
            elif cur < -0.0003:
                score += 8
                factors.append(f"Funding negative ({cur})")
            else:
                factors.append(f"Funding {cur}")
        except (TypeError, ValueError):
            pass
    elif funding.get("extreme_funding"):
        score -= 10
        factors.append("Funding extreme")

    oi = ctx.get("open_interest") or {}
    d24 = oi.get("delta_24h")
    if d24 is not None:
        try:
            d = float(d24)
            if d > 1.0:
                score += 8
                factors.append(f"OI rising ({d:+.1f}%)")
            elif d < -1.0:
                score -= 8
                factors.append(f"OI falling ({d:+.1f}%)")
        except (TypeError, ValueError):
            pass

    # Compressed top events polarity
    for ev in ((ctx.get("intelligence") or {}).get("top_events") or [])[:5]:
        pol = str(ev.get("polarity") or "").lower()
        impact = str(ev.get("impact") or ev.get("market_impact") or "MEDIUM").upper()
        w = {"CRITICAL": 10, "HIGH": 7, "MEDIUM": 4, "LOW": 2}.get(impact, 4)
        title = str(ev.get("title") or "")[:60]
        if pol == "bullish":
            score += w
            if title:
                factors.append(title)
        elif pol == "bearish":
            score -= w
            if title:
                factors.append(title)

    macro = ctx.get("macro") or {}
    dxy = macro.get("dxy") or {}
    if isinstance(dxy, dict):
        ch = dxy.get("change_pct") or dxy.get("change_1d_pct")
        if ch is not None:
            try:
                dc = float(ch)
                if dc > 0.3:
                    score -= 8
                    factors.append(f"DXY +{dc:.2f}%")
                elif dc < -0.3:
                    score += 6
                    factors.append(f"DXY {dc:.2f}%")
            except (TypeError, ValueError):
                pass

    # Clamp and decide
    score = max(-100.0, min(100.0, score))
    abs_score = abs(score)
    if abs_score < 12:
        direction = "WAIT"
    elif score > 0:
        direction = "LONG"
    else:
        direction = "SHORT"

    confidence_raw = min(92.0, 40.0 + abs_score * 0.55)
    # Dedupe factor strings while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for f in factors:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return DirectionDecision(
        direction=direction,
        score=round(score, 2),
        confidence_raw=round(confidence_raw, 1),
        factors=uniq[:8],
    )


def lock_direction(
    proposed: str | None,
    decision: DirectionDecision,
) -> str:
    """
    Force machine direction. LLM/proposed direction is ignored when decision is actionable.
    Returns LONG|SHORT|WAIT.
    """
    if decision.is_actionable:
        return decision.direction
    prop = str(proposed or "").upper().strip()
    if prop in ("LONG", "SHORT") and decision.direction == "WAIT":
        # Soft lock: do not allow LLM to invent a trade when score says WAIT
        return "WAIT"
    if prop in ("LONG", "SHORT"):
        return decision.direction if decision.is_actionable else "WAIT"
    return decision.direction
