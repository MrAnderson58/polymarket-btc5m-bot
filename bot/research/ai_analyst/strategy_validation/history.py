"""S48 — signal history records."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.ai_analyst.paper_trading.signals import TradingSignal


@dataclass
class SignalHistoryRecord:
    signal_id: str
    created_at: int
    market: str
    direction: str
    confidence: float
    score: float | None
    reasoning: str
    reasons: list[str]
    strategy: str
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_pct: float
    status: str = "PENDING"
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "created_at": self.created_at,
            "market": self.market,
            "direction": self.direction,
            "confidence": self.confidence,
            "score": self.score,
            "reasoning": self.reasoning,
            "reasons": list(self.reasons),
            "strategy": self.strategy,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "risk_pct": self.risk_pct,
            "status": self.status,
            "tags": list(self.tags),
        }


def score_from_signal(signal: TradingSignal) -> float:
    """Heuristic quality score 0–100 from confidence + R geometry."""
    mid = signal.entry_mid()
    risk = abs(mid - signal.stop_loss) or mid * 0.01
    reward = abs(signal.tp2 - mid)
    rr = reward / risk
    conf = max(0.0, min(100.0, float(signal.confidence)))
    rr_component = min(40.0, rr * 12.0)
    reason_bonus = min(10.0, len(signal.reasons) * 2.0)
    return round(min(100.0, conf * 0.5 + rr_component + reason_bonus), 2)


def tags_from_reasons(reasons: list[str]) -> list[str]:
    tags: list[str] = []
    blob = " ".join(reasons).lower()
    mapping = (
        ("etf", "ETF"),
        ("funding", "Funding"),
        ("macro", "Macro"),
        ("ai", "AI"),
        ("neutral", "Neutral Funding"),
        ("vix", "VIX"),
        ("dollar", "Dollar"),
        ("yield", "Yields"),
        ("risk-on", "Risk-On"),
        ("risk on", "Risk-On"),
        ("fear", "Fear"),
    )
    for needle, tag in mapping:
        if needle in blob and tag not in tags:
            tags.append(tag)
    return tags


def history_from_trading_signal(
    signal: TradingSignal,
    *,
    market: str | None = None,
    score: float | None = None,
    reasoning: str | None = None,
    tags: list[str] | None = None,
) -> SignalHistoryRecord:
    reasons = list(signal.reasons)
    return SignalHistoryRecord(
        signal_id=signal.signal_id,
        created_at=int(signal.created_at or time.time()),
        market=(market or signal.symbol or "BTC").upper(),
        direction=signal.direction,
        confidence=float(signal.confidence),
        score=float(score) if score is not None else score_from_signal(signal),
        reasoning=reasoning or "; ".join(reasons) or "context-derived",
        reasons=reasons,
        strategy=signal.strategy,
        entry_low=signal.entry_low,
        entry_high=signal.entry_high,
        stop_loss=signal.stop_loss,
        tp1=signal.tp1,
        tp2=signal.tp2,
        tp3=signal.tp3,
        risk_pct=signal.risk_pct,
        status=signal.status,
        tags=list(tags) if tags is not None else tags_from_reasons(reasons),
    )
