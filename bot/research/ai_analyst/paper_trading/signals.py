"""S47 — AI paper trading signal model."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TradingSignal:
    """Structured multi-target signal for paper execution."""

    symbol: str
    direction: str  # LONG | SHORT
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_pct: float
    confidence: float
    reasons: list[str] = field(default_factory=list)
    strategy: str = "ai_default"
    signal_id: str = ""
    created_at: int = 0
    status: str = "PENDING"  # PENDING | TRIGGERED | CANCELLED | EXPIRED

    def __post_init__(self) -> None:
        if not self.signal_id:
            self.signal_id = f"s47-{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = int(time.time())
        self.symbol = str(self.symbol).upper().strip()
        self.direction = str(self.direction).upper().strip()
        self.strategy = str(self.strategy or "ai_default").strip()
        self.reasons = [str(r).strip() for r in (self.reasons or []) if str(r).strip()]
        lo, hi = float(self.entry_low), float(self.entry_high)
        self.entry_low = min(lo, hi)
        self.entry_high = max(lo, hi)
        self.stop_loss = float(self.stop_loss)
        self.tp1 = float(self.tp1)
        self.tp2 = float(self.tp2)
        self.tp3 = float(self.tp3)
        self.risk_pct = float(self.risk_pct)
        self.confidence = float(self.confidence)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.direction not in ("LONG", "SHORT"):
            errors.append("direction must be LONG or SHORT")
        if self.entry_low <= 0 or self.entry_high <= 0:
            errors.append("entry range must be positive")
        if self.stop_loss <= 0:
            errors.append("stop_loss must be positive")
        if self.tp1 <= 0 or self.tp2 <= 0 or self.tp3 <= 0:
            errors.append("TP levels must be positive")
        if self.risk_pct <= 0 or self.risk_pct > 100:
            errors.append("risk_pct must be in (0, 100]")
        if self.confidence < 0 or self.confidence > 100:
            errors.append("confidence must be in [0, 100]")
        mid = (self.entry_low + self.entry_high) / 2.0
        if self.direction == "LONG":
            if self.stop_loss >= self.entry_low:
                errors.append("LONG stop_loss must be below entry_low")
            if not (self.tp1 > mid and self.tp2 >= self.tp1 and self.tp3 >= self.tp2):
                errors.append("LONG TPs must be above entry and ordered TP1<=TP2<=TP3")
        else:
            if self.stop_loss <= self.entry_high:
                errors.append("SHORT stop_loss must be above entry_high")
            if not (self.tp1 < mid and self.tp2 <= self.tp1 and self.tp3 <= self.tp2):
                errors.append("SHORT TPs must be below entry and ordered TP1>=TP2>=TP3")
        return errors

    def entry_mid(self) -> float:
        return round((self.entry_low + self.entry_high) / 2.0, 8)

    def risk_distance(self) -> float:
        mid = self.entry_mid()
        return abs(mid - self.stop_loss)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradingSignal":
        reasons = data.get("reasons") or []
        if isinstance(reasons, str):
            try:
                reasons = json.loads(reasons)
            except json.JSONDecodeError:
                reasons = [reasons]
        return cls(
            symbol=str(data["symbol"]),
            direction=str(data["direction"]),
            entry_low=float(data["entry_low"]),
            entry_high=float(data["entry_high"]),
            stop_loss=float(data["stop_loss"]),
            tp1=float(data["tp1"]),
            tp2=float(data["tp2"]),
            tp3=float(data["tp3"]),
            risk_pct=float(data.get("risk_pct") or 1.0),
            confidence=float(data.get("confidence") or 0.0),
            reasons=list(reasons),
            strategy=str(data.get("strategy") or "ai_default"),
            signal_id=str(data.get("signal_id") or ""),
            created_at=int(data.get("created_at") or 0),
            status=str(data.get("status") or "PENDING"),
        )


def price_in_entry_range(price: float, signal: TradingSignal) -> bool:
    return signal.entry_low <= float(price) <= signal.entry_high


def fill_entry_price(price: float, signal: TradingSignal) -> float:
    """Fill at touch price clamped into the entry band."""
    p = float(price)
    return max(signal.entry_low, min(signal.entry_high, p))
