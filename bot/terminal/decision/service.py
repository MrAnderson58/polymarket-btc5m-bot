"""DecisionService — Scanner → DecisionCard facade."""

from __future__ import annotations

from bot.terminal.decision.builder import build_from_scan
from bot.terminal.decision.models import DecisionCard, LearningHint
from bot.terminal.instruments.profiles import get_market_profile
from bot.terminal.models.dto import SignalCard
from bot.terminal.scanner.scanner import ScannerService, get_scanner_service
from bot.terminal.services.portfolio_service import get_portfolio_service
from bot.terminal.services.signals_service import get_signal_service


class DecisionService:
    """Build DecisionCards from live Scanner (+ optional signal / learning hints)."""

    def __init__(self, *, scanner: ScannerService | None = None) -> None:
        self._scanner = scanner or get_scanner_service()

    def _learning_hint(self) -> LearningHint | None:
        try:
            card = get_portfolio_service().get_summary()
            wr = card.winrate_pct
            if wr is None:
                return LearningHint(note="learning unavailable")
            confirms = wr >= 50.0
            return LearningHint(
                confirms=confirms,
                winrate_pct=float(wr),
                note=f"paper winrate {wr:.1f}%",
            )
        except Exception:
            return None

    def _signal_for(self, symbol: str) -> SignalCard | None:
        try:
            needle = symbol.upper().replace("USDT", "")
            for s in get_signal_service().get_top(limit=10):
                if s.symbol.upper().replace("USDT", "") == needle:
                    return s
        except Exception:
            return None
        return None

    def for_symbol(self, symbol: str, *, price: float | None = None) -> DecisionCard | None:
        scan = self._scanner.by_symbol(symbol)
        if scan is None:
            return None
        try:
            profile = get_market_profile(scan.asset_class)
        except Exception:
            profile = None
        return build_from_scan(
            scan,
            profile=profile,
            signal=self._signal_for(symbol),
            learning=self._learning_hint(),
            price=price,
        )

    def from_scan(
        self,
        symbol: str | None = None,
        *,
        price: float | None = None,
    ) -> DecisionCard | None:
        """Alias for for_symbol; if symbol omitted → best scanner hit."""
        if symbol:
            return self.for_symbol(symbol, price=price)
        top = self._scanner.top(1)
        if not top:
            return None
        return self.for_symbol(top[0].symbol, price=price)

    def top(self, limit: int = 5, *, price_by_symbol: dict[str, float] | None = None) -> list[DecisionCard]:
        cards: list[DecisionCard] = []
        prices = price_by_symbol or {}
        for row in self._scanner.top(limit):
            px = prices.get(row.symbol) or prices.get(row.symbol.upper())
            card = build_from_scan(
                row,
                profile=get_market_profile(row.asset_class),
                signal=self._signal_for(row.symbol),
                learning=self._learning_hint(),
                price=px,
            )
            cards.append(card)
        return cards


_default: DecisionService | None = None


def get_decision_service(*, scanner: ScannerService | None = None) -> DecisionService:
    global _default
    if scanner is not None:
        return DecisionService(scanner=scanner)
    if _default is None:
        _default = DecisionService()
    return _default


def reset_decision_service() -> None:
    global _default
    _default = None


__all__ = [
    "DecisionService",
    "get_decision_service",
    "reset_decision_service",
]
