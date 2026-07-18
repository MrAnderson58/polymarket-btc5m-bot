"""Terminal DTOs — display cards only, no business logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SignalCard:
    symbol: str
    direction: str
    status: str = "unknown"
    confidence: float | None = None
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionCard:
    symbol: str
    side: str
    size: float | None = None
    entry: float | None = None
    stop: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    risk: float | None = None
    status: str = "open"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioCard:
    equity: float | None = None
    cash: float | None = None
    used_margin: float | None = None
    open_risk: float | None = None
    open_positions: int = 0
    today_pnl: float | None = None
    week_pnl: float | None = None
    winrate_pct: float | None = None
    trades: int = 0
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccountCard:
    mode: str = "paper"
    balance: float | None = None
    equity: float | None = None
    status: str = "ok"
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketGroupCard:
    name: str
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketsCard:
    groups: tuple[MarketGroupCard, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class WorkerStatusCard:
    name: str
    online: bool
    detail: str = ""


@dataclass(frozen=True)
class SystemStatusCard:
    online: bool
    workers: tuple[WorkerStatusCard, ...] = ()
    dashboard: str = ""
    telegram: str = ""
    learning: str = ""
    decision: str = ""
    pattern: str = ""
    news: str = ""
    summary: str = ""


@dataclass(frozen=True)
class HomeCard:
    system_online: bool
    mode: str
    equity: float | None = None
    balance: float | None = None
    open_positions: int = 0
    today_pnl: float | None = None
    best_signal: str = "—"
    last_ai_decision: str = "—"
    workers_line: str = "—"
    dashboard_line: str = "—"
    system: SystemStatusCard | None = None
    summary: str = ""


__all__ = [
    "AccountCard",
    "HomeCard",
    "MarketGroupCard",
    "MarketsCard",
    "PortfolioCard",
    "PositionCard",
    "SignalCard",
    "SystemStatusCard",
    "WorkerStatusCard",
]
