"""Instrument registry — unified catalog over providers."""

from __future__ import annotations

from typing import Iterable, Sequence

from bot.terminal.instruments.models import AssetClass, AssetClassName, Instrument
from bot.terminal.instruments.providers import InstrumentProvider, StaticProvider


def _norm(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("-", "").strip()


def _as_asset_class(value: AssetClass | AssetClassName | str) -> AssetClass:
    if isinstance(value, AssetClass):
        return value
    return AssetClass(str(value).strip().lower())


class InstrumentRegistry:
    """In-memory instrument catalog. No SQL / network."""

    def __init__(self, providers: Sequence[InstrumentProvider] | None = None) -> None:
        self._by_symbol: dict[str, Instrument] = {}
        for provider in providers or (StaticProvider(),):
            self._ingest(provider.load())

    def _ingest(self, instruments: Iterable[Instrument]) -> None:
        for inst in instruments:
            self._by_symbol[_norm(inst.symbol)] = inst

    def get(self, symbol: str) -> Instrument | None:
        """Return instrument by symbol, or None if missing."""
        return self._by_symbol.get(_norm(symbol))

    def exists(self, symbol: str) -> bool:
        """True if symbol is registered."""
        return _norm(symbol) in self._by_symbol

    def list(
        self,
        asset_class: AssetClass | AssetClassName | str | None = None,
    ) -> list[Instrument]:
        """List all instruments, optionally filtered by asset class."""
        items = list(self._by_symbol.values())
        if asset_class is None:
            return sorted(items, key=lambda i: i.symbol)
        ac = _as_asset_class(asset_class)
        return sorted(
            (i for i in items if i.asset_class == ac),
            key=lambda i: i.symbol,
        )


_default_registry: InstrumentRegistry | None = None


def get_instrument_registry() -> InstrumentRegistry:
    """Process-wide default registry (StaticProvider)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = InstrumentRegistry()
    return _default_registry


__all__ = ["InstrumentRegistry", "get_instrument_registry"]
