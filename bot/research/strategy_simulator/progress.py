"""Discovery progress reporting."""

from __future__ import annotations

import sys
import time


def format_eta(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "--:--"
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class DiscoveryProgress:
    def __init__(self, *, total_markets: int, total_strategies: int) -> None:
        self.total_markets = total_markets
        self.total_strategies = total_strategies
        self.total_units = max(1, total_markets * total_strategies)
        self.market_i = 0
        self.strategy_i = 0
        self._start = time.monotonic()
        self._last_render = 0.0

    def set_market(self, market_i: int) -> None:
        self.market_i = market_i
        self.strategy_i = 0
        self._maybe_render(force=True)

    def tick_strategy(self, strategy_i: int) -> None:
        self.strategy_i = strategy_i
        self._maybe_render()

    def _done_units(self) -> int:
        return (self.market_i - 1) * self.total_strategies + self.strategy_i

    def _eta_seconds(self) -> float:
        done = self._done_units()
        if done <= 0:
            return float("inf")
        elapsed = time.monotonic() - self._start
        return elapsed / done * (self.total_units - done)

    def _maybe_render(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_render < 0.25:
            return
        self._last_render = now
        line = (
            f"Markets: {self.market_i}/{self.total_markets} | "
            f"Strategies: {self.strategy_i}/{self.total_strategies} | "
            f"ETA: {format_eta(self._eta_seconds())}"
        )
        sys.stderr.write("\r" + line.ljust(72))
        sys.stderr.flush()

    def finish(self) -> None:
        sys.stderr.write("\n")
        sys.stderr.flush()
