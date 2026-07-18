"""Scanner providers — Crypto (G3.1) + Static (tests)."""

from __future__ import annotations

from typing import Protocol, Sequence

from bot.terminal.instruments.models import AssetClass
from bot.terminal.scanner.models import RankComponents, ScannerResult
from bot.terminal.scanner.ranking import compute_score, normalize_confidence


class ScannerProvider(Protocol):
    """Pluggable scan source for one or more asset classes."""

    name: str

    def supports(self, asset_class: AssetClass | str) -> bool:
        """True if this provider covers the asset class."""
        ...

    def priority(self) -> int:
        """Higher = preferred when multiple providers match."""
        ...

    def scan(self, *, limit: int = 50) -> Sequence[ScannerResult]:
        """Return normalized scan results (read-only)."""
        ...


def _as_asset_class(value: AssetClass | str) -> AssetClass:
    if isinstance(value, AssetClass):
        return value
    return AssetClass(str(value).strip().lower())


class CryptoScannerProvider:
    """Uses existing G3.1 candidates — no new scoring logic."""

    name = "g31_crypto"

    def supports(self, asset_class: AssetClass | str) -> bool:
        ac = _as_asset_class(asset_class)
        return ac in {AssetClass.CRYPTO, AssetClass.CRYPTO_FUTURE}

    def priority(self) -> int:
        return 100

    def scan(self, *, limit: int = 50) -> Sequence[ScannerResult]:
        try:
            from bot.research.market_events.signal_intelligence.candidate_g31 import (
                fetch_top_candidates_g31,
            )
            from bot.terminal.services._db import market_events_ro

            with market_events_ro() as conn:
                rows = fetch_top_candidates_g31(conn, limit=limit)
        except Exception:
            return []

        out: list[ScannerResult] = []
        for r in rows:
            symbol = str(r["symbol"])
            direction = "—"
            if "direction" in r.keys() and r["direction"] is not None:
                direction = str(r["direction"])
            conf_raw = r["confidence"] if "confidence" in r.keys() else None
            conf = float(conf_raw) if conf_raw is not None else None
            market_score = None
            if "market_score" in r.keys() and r["market_score"] is not None:
                market_score = float(r["market_score"])
            trend = None
            if "trend_coverage_pct" in r.keys() and r["trend_coverage_pct"] is not None:
                trend = float(r["trend_coverage_pct"])
            volume = None
            if "liquidity_score" in r.keys() and r["liquidity_score"] is not None:
                volume = float(r["liquidity_score"])
                if volume <= 10:
                    volume = volume * 10.0

            reasons: list[str] = []
            if "candidate_state" in r.keys() and r["candidate_state"]:
                reasons.append(str(r["candidate_state"]))
            if "rejection_reason" in r.keys() and r["rejection_reason"]:
                reasons.append(str(r["rejection_reason"]))

            components = RankComponents(
                confidence=normalize_confidence(conf),
                ai=_clamp_score(market_score),
                learning=None,
                trend=_clamp_score(trend),
                volume=_clamp_score(volume),
                news=None,
                pattern=None,
            )
            out.append(
                ScannerResult(
                    symbol=symbol,
                    asset_class=AssetClass.CRYPTO_FUTURE,
                    direction=direction,
                    confidence=conf,
                    score=compute_score(components),
                    reasons=tuple(reasons),
                    provider=self.name,
                    components=components,
                    extra={"source": "fetch_top_candidates_g31"},
                )
            )
        return out


def _clamp_score(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))


class StaticScannerProvider:
    """Deterministic fixtures for tests — no DB / network."""

    name = "static"

    def __init__(self, results: Sequence[ScannerResult] | None = None) -> None:
        self._results = list(results or _default_static_results())

    def supports(self, asset_class: AssetClass | str) -> bool:
        ac = _as_asset_class(asset_class)
        return any(r.asset_class == ac for r in self._results) or ac in {
            AssetClass.CRYPTO,
            AssetClass.CRYPTO_FUTURE,
        }

    def priority(self) -> int:
        return 10

    def scan(self, *, limit: int = 50) -> Sequence[ScannerResult]:
        return list(self._results)[: max(0, limit)]


def _default_static_results() -> list[ScannerResult]:
    samples = (
        ("BTC", "LONG", 9.0, 92.0),
        ("ETH", "LONG", 8.5, 88.0),
        ("SOL", "SHORT", 8.0, 84.0),
        ("XRP", "LONG", 7.0, 70.0),
        ("BNB", "FLAT", 6.0, 60.0),
    )
    out: list[ScannerResult] = []
    for symbol, direction, conf, score in samples:
        components = RankComponents(
            confidence=normalize_confidence(conf),
            ai=score,
            trend=score,
        )
        out.append(
            ScannerResult(
                symbol=symbol,
                asset_class=AssetClass.CRYPTO_FUTURE,
                direction=direction,
                confidence=conf,
                score=compute_score(components),
                reasons=("static",),
                provider="static",
                components=components,
            )
        )
    return out


__all__ = [
    "CryptoScannerProvider",
    "ScannerProvider",
    "StaticScannerProvider",
]
