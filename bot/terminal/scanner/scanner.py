"""ScannerService — unified scan / top / by_symbol API."""

from __future__ import annotations

from bot.terminal.instruments.models import AssetClass
from bot.terminal.scanner.models import ScannerResult
from bot.terminal.scanner.registry import ScannerRegistry, get_scanner_registry


def _as_asset_class(value: AssetClass | str) -> AssetClass:
    if isinstance(value, AssetClass):
        return value
    return AssetClass(str(value).strip().lower())


class ScannerService:
    """Facade over ScannerRegistry providers."""

    def __init__(self, registry: ScannerRegistry | None = None) -> None:
        self._registry = registry or get_scanner_registry()

    def scan(self, asset_class: AssetClass | str, *, limit: int = 50) -> list[ScannerResult]:
        """Scan a single asset class via the best matching provider."""
        ac = _as_asset_class(asset_class)
        provider = self._registry.provider(ac)
        if provider is None:
            return []
        rows = list(provider.scan(limit=limit))
        return [r for r in rows if r.asset_class == ac or self._crypto_alias(ac, r.asset_class)]

    @staticmethod
    def _crypto_alias(requested: AssetClass, got: AssetClass) -> bool:
        group = {AssetClass.CRYPTO, AssetClass.CRYPTO_FUTURE}
        return requested in group and got in group

    def scan_all(self, *, limit_per_provider: int = 50) -> list[ScannerResult]:
        """Merge results from all providers (dedupe by symbol, keep highest score)."""
        by_symbol: dict[str, ScannerResult] = {}
        for provider in self._registry.providers():
            for row in provider.scan(limit=limit_per_provider):
                key = row.symbol.upper()
                prev = by_symbol.get(key)
                if prev is None or row.score > prev.score:
                    by_symbol[key] = row
        return sorted(by_symbol.values(), key=lambda r: r.score, reverse=True)

    def top(self, limit: int = 5, *, asset_class: AssetClass | str | None = None) -> list[ScannerResult]:
        """TOP-N by unified score."""
        rows = self.scan(asset_class, limit=max(limit * 3, 20)) if asset_class else self.scan_all()
        return sorted(rows, key=lambda r: r.score, reverse=True)[: max(0, limit)]

    def by_symbol(self, symbol: str) -> ScannerResult | None:
        """Best result for a symbol across providers."""
        needle = symbol.upper().replace("USDT", "")
        for row in self.scan_all():
            sym = row.symbol.upper().replace("USDT", "")
            if sym == needle or row.symbol.upper() == symbol.upper():
                return row
        return None


def get_scanner_service() -> ScannerService:
    return ScannerService()


__all__ = ["ScannerService", "get_scanner_service"]
