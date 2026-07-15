"""X / Twitter intake interface — stub for future API wiring (S2.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Whitelist for later API wiring — not fetched unless a client is injected.
DEFAULT_X_ACCOUNT_WHITELIST: tuple[str, ...] = (
    "woonomic",
    "lookonchain",
    "CryptoQuant_com",
    "glassnode",
    "TheBlock__",
    "CoinDesk",
    "kaikaroostami",
    "CryptoHayes",
)


@dataclass
class XTweetStub:
    account: str
    text: str
    url: str | None = None
    ts: int | None = None


@dataclass
class XClientInterface:
    """Abstract X source. Production implementations inject a real API client."""

    whitelist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_X_ACCOUNT_WHITELIST)

    def fetch_recent(self, *, symbol: str, limit: int = 20) -> list[XTweetStub]:
        """Default: no API — return empty. Override / inject client later."""
        return []

    def is_configured(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "whitelist": list(self.whitelist),
            "note": "X interface ready; API client not wired",
        }
