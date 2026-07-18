"""Universal Scanner DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.terminal.instruments.models import AssetClass


@dataclass(frozen=True)
class RankComponents:
    """Feature inputs for unified ranking (0–100 each when present)."""

    confidence: float | None = None
    ai: float | None = None
    learning: float | None = None
    trend: float | None = None
    volume: float | None = None
    news: float | None = None
    pattern: float | None = None


@dataclass(frozen=True)
class ScannerResult:
    """Normalized scan hit from any ScannerProvider."""

    symbol: str
    asset_class: AssetClass
    direction: str
    confidence: float | None
    score: float
    reasons: tuple[str, ...] = ()
    provider: str = ""
    components: RankComponents = field(default_factory=RankComponents)
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = ["RankComponents", "ScannerResult"]
