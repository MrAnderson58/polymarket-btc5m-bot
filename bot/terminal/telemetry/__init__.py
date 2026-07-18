"""Terminal Telemetry (V7.1.4)."""

from __future__ import annotations

import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class TelemetrySnapshot:
    decision_build_ms_total: float = 0.0
    decision_build_count: int = 0
    scan_ms_total: float = 0.0
    scan_count: int = 0
    signals_viewed: int = 0
    decision_cards_opened: int = 0
    screen_views: Counter[str] = field(default_factory=Counter)

    @property
    def avg_decision_build_ms(self) -> float:
        if self.decision_build_count <= 0:
            return 0.0
        return self.decision_build_ms_total / self.decision_build_count

    @property
    def avg_scan_ms(self) -> float:
        if self.scan_count <= 0:
            return 0.0
        return self.scan_ms_total / self.scan_count


class Telemetry:
    def __init__(self) -> None:
        self._snap = TelemetrySnapshot()

    def reset(self) -> None:
        self._snap = TelemetrySnapshot()

    def snapshot(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            decision_build_ms_total=self._snap.decision_build_ms_total,
            decision_build_count=self._snap.decision_build_count,
            scan_ms_total=self._snap.scan_ms_total,
            scan_count=self._snap.scan_count,
            signals_viewed=self._snap.signals_viewed,
            decision_cards_opened=self._snap.decision_cards_opened,
            screen_views=Counter(self._snap.screen_views),
        )

    def record_screen(self, screen: str) -> None:
        self._snap.screen_views[screen] += 1

    def record_signals_viewed(self, n: int = 1) -> None:
        self._snap.signals_viewed += max(0, n)

    def record_decision_opened(self) -> None:
        self._snap.decision_cards_opened += 1

    def record_decision_build_ms(self, ms: float) -> None:
        self._snap.decision_build_ms_total += max(0.0, ms)
        self._snap.decision_build_count += 1

    def record_scan_ms(self, ms: float) -> None:
        self._snap.scan_ms_total += max(0.0, ms)
        self._snap.scan_count += 1

    @contextmanager
    def measure_decision(self) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.record_decision_build_ms((time.perf_counter() - t0) * 1000.0)

    @contextmanager
    def measure_scan(self) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.record_scan_ms((time.perf_counter() - t0) * 1000.0)


_default: Telemetry | None = None


def get_telemetry() -> Telemetry:
    global _default
    if _default is None:
        _default = Telemetry()
    return _default


def reset_telemetry() -> None:
    global _default
    if _default is not None:
        _default.reset()
    _default = None


__all__ = [
    "Telemetry",
    "TelemetrySnapshot",
    "get_telemetry",
    "reset_telemetry",
]
