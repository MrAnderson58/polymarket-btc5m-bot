"""Exposure classification — sector / crypto weights from positions."""

from __future__ import annotations

from bot.terminal.instruments.models import AssetClass
from bot.terminal.instruments.registry import InstrumentRegistry, get_instrument_registry
from bot.terminal.models.dto import PositionCard
from bot.terminal.portfolio.models import ExposureSlice, PositionWeight


_CRYPTO_CLASSES = {AssetClass.CRYPTO, AssetClass.CRYPTO_FUTURE}

_SECTOR_LABELS: dict[AssetClass, str] = {
    AssetClass.CRYPTO: "Crypto",
    AssetClass.CRYPTO_FUTURE: "Crypto",
    AssetClass.STOCK: "Equities",
    AssetClass.ETF: "ETF",
    AssetClass.INDEX: "Index",
    AssetClass.COMMODITY: "Commodities",
    AssetClass.FOREX: "FX",
}

_SYMBOL_ALIASES: dict[str, str] = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "XAU": "GOLD",
    "XAUUSD": "GOLD",
    "GLD": "GOLD",
}


def normalize_symbol(raw: str) -> str:
    s = (raw or "").upper().replace("/", "").replace("-", "").strip()
    return _SYMBOL_ALIASES.get(s, s.replace("USDT", "") or s)


def classify_sector(symbol: str, registry: InstrumentRegistry | None = None) -> str:
    """Map symbol → sector label via instrument registry (+ heuristics)."""
    reg = registry or get_instrument_registry()
    sym = normalize_symbol(symbol)
    # Try registry with common suffixes
    for candidate in (symbol, sym, f"{sym}USDT", "XAUUSD" if sym == "GOLD" else sym, "GLD" if sym == "GOLD" else sym):
        inst = reg.get(candidate)
        if inst is not None:
            return _SECTOR_LABELS.get(inst.asset_class, inst.asset_class.value.title())
    # Heuristics when not registered
    if sym in {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA"}:
        return "Crypto"
    if sym in {"GOLD", "XAU", "SILVER", "XAG", "CL", "OIL", "WTI"}:
        return "Commodities"
    if sym in {"EURUSD", "GBPUSD", "USDJPY", "FX"}:
        return "FX"
    return "Other"


def position_notionals(positions: list[PositionCard] | tuple[PositionCard, ...]) -> list[tuple[PositionCard, float]]:
    out: list[tuple[PositionCard, float]] = []
    for p in positions:
        if p.status == "unavailable" or p.symbol in {"", "—"}:
            continue
        size = float(p.size) if p.size is not None else 0.0
        if size <= 0:
            continue
        out.append((p, size))
    return out


def build_weights(
    positions: list[PositionCard] | tuple[PositionCard, ...],
    *,
    equity: float | None,
    registry: InstrumentRegistry | None = None,
) -> tuple[PositionWeight, ...]:
    pairs = position_notionals(positions)
    total = sum(n for _, n in pairs)
    if total <= 0:
        return ()
    base = float(equity) if equity and equity > 0 else total
    reg = registry or get_instrument_registry()
    weights: list[PositionWeight] = []
    for p, notional in pairs:
        sym = normalize_symbol(p.symbol)
        sector = classify_sector(p.symbol, reg)
        weights.append(
            PositionWeight(
                symbol=sym,
                side=(p.side or "—").upper(),
                weight_pct=round(100.0 * notional / base, 1),
                notional=round(notional, 2),
                sector=sector,
            )
        )
    return tuple(sorted(weights, key=lambda w: w.weight_pct, reverse=True))


def sector_exposure(weights: tuple[PositionWeight, ...]) -> tuple[ExposureSlice, ...]:
    buckets: dict[str, list[PositionWeight]] = {}
    for w in weights:
        buckets.setdefault(w.sector, []).append(w)
    slices: list[ExposureSlice] = []
    for label, items in buckets.items():
        pct = round(sum(i.weight_pct for i in items), 1)
        notional = round(sum(i.notional for i in items), 2)
        symbols = tuple(i.symbol for i in items)
        slices.append(ExposureSlice(label=label, weight_pct=pct, symbols=symbols, notional=notional))
    return tuple(sorted(slices, key=lambda s: s.weight_pct, reverse=True))


def crypto_exposure_pct(weights: tuple[PositionWeight, ...]) -> float:
    return round(sum(w.weight_pct for w in weights if w.sector == "Crypto"), 1)


__all__ = [
    "build_weights",
    "classify_sector",
    "crypto_exposure_pct",
    "normalize_symbol",
    "sector_exposure",
]
