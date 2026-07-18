"""SignalService — top signals via Universal Scanner (G3.1 under CryptoProvider)."""

from __future__ import annotations

from typing import Protocol

from bot.terminal.models.dto import SignalCard


class SignalService(Protocol):
    def get_top(self, *, limit: int = 5) -> list[SignalCard]:
        ...


class DefaultSignalService:
    def get_top(self, *, limit: int = 5) -> list[SignalCard]:
        try:
            from bot.terminal.scanner import get_scanner_service

            rows = get_scanner_service().top(limit, asset_class="crypto")
            if not rows:
                # also try crypto_future alias group via scan_all top
                rows = get_scanner_service().top(limit)
            out: list[SignalCard] = []
            for r in rows[:limit]:
                out.append(
                    SignalCard(
                        symbol=r.symbol,
                        direction=r.direction or "—",
                        status=r.reasons[0] if r.reasons else "scanned",
                        confidence=r.confidence,
                        summary="; ".join(r.reasons) if r.reasons else f"score={r.score}",
                        extra={
                            "source": "ScannerService",
                            "provider": r.provider,
                            "score": r.score,
                        },
                    )
                )
            return out
        except Exception as exc:
            return [
                SignalCard(
                    symbol="—",
                    direction="—",
                    status="unavailable",
                    summary=str(exc),
                    extra={"error": str(exc), "source": "fallback"},
                )
            ]

    def list_signals(self, *, limit: int = 10) -> list[SignalCard]:
        return self.get_top(limit=limit)


def get_signal_service() -> SignalService:
    return DefaultSignalService()


__all__ = ["DefaultSignalService", "SignalService", "get_signal_service"]
