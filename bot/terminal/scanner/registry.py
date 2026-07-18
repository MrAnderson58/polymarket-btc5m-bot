"""Scanner provider registry."""

from __future__ import annotations

from bot.terminal.instruments.models import AssetClass
from bot.terminal.scanner.providers import (
    CryptoScannerProvider,
    ScannerProvider,
    StaticScannerProvider,
)


def _as_asset_class(value: AssetClass | str) -> AssetClass:
    if isinstance(value, AssetClass):
        return value
    return AssetClass(str(value).strip().lower())


class ScannerRegistry:
    """Register and resolve ScannerProvider instances by asset class."""

    def __init__(self) -> None:
        self._providers: list[ScannerProvider] = []

    def register(self, provider: ScannerProvider) -> None:
        """Add a provider (idempotent by name)."""
        names = {getattr(p, "name", id(p)) for p in self._providers}
        key = getattr(provider, "name", id(provider))
        if key in names:
            return
        self._providers.append(provider)

    def providers(self) -> list[ScannerProvider]:
        """All registered providers, highest priority first."""
        return sorted(self._providers, key=lambda p: p.priority(), reverse=True)

    def provider(self, asset_class: AssetClass | str) -> ScannerProvider | None:
        """Best provider that supports the asset class."""
        ac = _as_asset_class(asset_class)
        matches = [p for p in self._providers if p.supports(ac)]
        if not matches:
            return None
        return max(matches, key=lambda p: p.priority())


def default_scanner_registry(*, include_static: bool = False) -> ScannerRegistry:
    """Production registry: G3.1 crypto provider (+ optional static for tests)."""
    reg = ScannerRegistry()
    reg.register(CryptoScannerProvider())
    if include_static:
        reg.register(StaticScannerProvider())
    return reg


_default: ScannerRegistry | None = None


def get_scanner_registry() -> ScannerRegistry:
    global _default
    if _default is None:
        _default = default_scanner_registry(include_static=False)
    return _default


__all__ = [
    "ScannerRegistry",
    "default_scanner_registry",
    "get_scanner_registry",
]
