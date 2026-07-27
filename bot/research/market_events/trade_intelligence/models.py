"""Trade Intelligence V1 dataclasses — future AI plugs into these shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SOURCES = ("paper", "manual", "telegram", "csv")
STATUSES = ("open", "closed", "cancelled")


@dataclass
class TradeRecord:
    id: int | None
    source: str
    external_id: str | None
    symbol: str
    side: str
    entry_ts: int | None = None
    exit_ts: int | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    size: float | None = None
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    strategy: str | None = None
    status: str = "closed"
    raw_json: dict[str, Any] = field(default_factory=dict)
    created_at: int | None = None
    updated_at: int | None = None


@dataclass
class MarketSnapshot:
    id: int | None = None
    trade_id: int | None = None
    snapshot_ts: int | None = None
    price: float | None = None
    funding: float | None = None
    oi: float | None = None
    volatility: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsItem:
    id: int | None = None
    trade_id: int | None = None
    news_ts: int | None = None
    title: str = ""
    summary: str = ""
    source: str | None = None
    url: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TelegramItem:
    id: int | None = None
    trade_id: int | None = None
    message_ts: int | None = None
    channel: str | None = None
    message_id: str | None = None
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextItem:
    id: int | None = None
    trade_id: int | None = None
    context_key: str = ""
    context_value: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    id: int | None = None
    trade_id: int | None = None
    outcome_ts: int | None = None
    result: str | None = None  # win|loss|breakeven|unknown
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    exit_reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AISummary:
    """Placeholder for future LLM summaries — stored but never generated in V1."""

    id: int | None = None
    trade_id: int | None = None
    model: str | None = None
    summary_text: str = ""
    created_at: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tag:
    id: int | None = None
    trade_id: int | None = None
    tag: str = ""


@dataclass
class Note:
    id: int | None = None
    trade_id: int | None = None
    note_ts: int | None = None
    author: str | None = None
    text: str = ""


@dataclass
class TradeKnowledge:
    """Full knowledge envelope for one trade — AI layer attaches here later."""

    trade: TradeRecord
    market_snapshots: list[MarketSnapshot] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    telegram: list[TelegramItem] = field(default_factory=list)
    context: list[ContextItem] = field(default_factory=list)
    outcome: Outcome | None = None
    ai_summary: AISummary | None = None
    tags: list[Tag] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade": self.trade.__dict__,
            "market_snapshots": [s.__dict__ for s in self.market_snapshots],
            "news": [n.__dict__ for n in self.news],
            "telegram": [t.__dict__ for t in self.telegram],
            "context": [c.__dict__ for c in self.context],
            "outcome": self.outcome.__dict__ if self.outcome else None,
            "ai_summary": self.ai_summary.__dict__ if self.ai_summary else None,
            "tags": [t.__dict__ for t in self.tags],
            "notes": [n.__dict__ for n in self.notes],
        }
