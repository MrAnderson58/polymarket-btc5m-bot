"""S47 — paper trade models and metrics helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


STATUS_PENDING = "PENDING"
STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"

EXIT_TP1 = "TP1"
EXIT_TP2 = "TP2"
EXIT_TP3 = "TP3"
EXIT_STOP = "STOP"
EXIT_MANUAL = "MANUAL"

# Fraction closed at each target (remainder on TP3 / stop)
TP1_FRACTION = 1.0 / 3.0
TP2_FRACTION = 1.0 / 3.0
TP3_FRACTION = 1.0 / 3.0


@dataclass
class Fill:
    level: str
    price: float
    fraction: float
    pnl_usd: float
    r_multiple: float
    ts: int


@dataclass
class PaperTrade:
    trade_id: str
    signal_id: str
    symbol: str
    direction: str
    strategy: str
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_pct: float
    confidence: float
    reasons: list[str]
    opened_at: int
    status: str = STATUS_OPEN
    size_remaining: float = 1.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    pnl_usd: float = 0.0
    r_multiple: float = 0.0
    holding_seconds: int = 0
    closed_at: int | None = None
    exit_reason: str | None = None
    exit_price: float | None = None
    fills: list[Fill] = field(default_factory=list)
    account_equity_at_open: float = 10000.0

    def risk_distance(self) -> float:
        d = abs(self.entry - self.stop_loss)
        return d if d > 0 else self.entry * 0.01

    def risk_usd(self) -> float:
        return abs(self.account_equity_at_open) * (self.risk_pct / 100.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "strategy": self.strategy,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "risk_pct": self.risk_pct,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "opened_at": self.opened_at,
            "status": self.status,
            "size_remaining": self.size_remaining,
            "tp1_hit": self.tp1_hit,
            "tp2_hit": self.tp2_hit,
            "tp3_hit": self.tp3_hit,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "mfe_r": self.mfe_r,
            "mae_r": self.mae_r,
            "pnl_usd": self.pnl_usd,
            "r_multiple": self.r_multiple,
            "holding_seconds": self.holding_seconds,
            "closed_at": self.closed_at,
            "exit_reason": self.exit_reason,
            "exit_price": self.exit_price,
            "fills": [
                {
                    "level": f.level,
                    "price": f.price,
                    "fraction": f.fraction,
                    "pnl_usd": f.pnl_usd,
                    "r_multiple": f.r_multiple,
                    "ts": f.ts,
                }
                for f in self.fills
            ],
            "account_equity_at_open": self.account_equity_at_open,
        }


def price_pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def price_to_r(entry: float, price: float, stop: float, *, is_long: bool) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        risk = entry * 0.01
    move = (price - entry) if is_long else (entry - price)
    return move / risk


def tp_hit(is_long: bool, price: float, level: float) -> bool:
    return price >= level if is_long else price <= level


def sl_hit(is_long: bool, price: float, sl: float) -> bool:
    return price <= sl if is_long else price >= sl


def compute_stats(trades: list[PaperTrade]) -> dict[str, Any]:
    closed = [t for t in trades if t.status == STATUS_CLOSED]
    if not closed:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_r": 0.0,
            "total_pnl_usd": 0.0,
            "avg_hold_seconds": 0.0,
            "avg_mfe_pct": 0.0,
            "avg_mae_pct": 0.0,
        }

    wins = [t for t in closed if t.pnl_usd > 0]
    losses = [t for t in closed if t.pnl_usd < 0]
    be = [t for t in closed if t.pnl_usd == 0]
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    if gross_loss > 0:
        pf = gross_profit / gross_loss
    else:
        pf = float("inf") if gross_profit > 0 else 0.0

    total_pnl = sum(t.pnl_usd for t in closed)
    avg_r = sum(t.r_multiple for t in closed) / len(closed)
    # Expectancy in R units
    expectancy = avg_r
    win_rate = len(wins) / len(closed)

    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
        "win_rate": round(win_rate * 100.0, 2),
        "profit_factor": round(pf, 4) if pf != float("inf") else None,
        "profit_factor_raw": pf,
        "expectancy": round(expectancy, 4),
        "avg_r": round(avg_r, 4),
        "total_pnl_usd": round(total_pnl, 4),
        "avg_hold_seconds": round(sum(t.holding_seconds for t in closed) / len(closed), 1),
        "avg_mfe_pct": round(sum(t.mfe_pct for t in closed) / len(closed), 4),
        "avg_mae_pct": round(sum(t.mae_pct for t in closed) / len(closed), 4),
    }
