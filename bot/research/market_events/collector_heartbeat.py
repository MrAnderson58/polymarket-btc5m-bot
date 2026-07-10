"""Collector heartbeat formatting and cycle metrics."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.detector_diagnostics import DetectorDiagnostics


def format_uptime(seconds: int) -> str:
    h, rem = divmod(max(0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(len(ordered) * pct / 100.0)
    idx = min(idx, len(ordered) - 1)
    return ordered[idx]


@dataclass
class CollectorMetrics:
    startup_ts: int = field(default_factory=lambda: int(time.time()))
    cycles: int = 0
    fetch_ok: int = 0
    fetch_failed: int = 0
    events_detected: int = 0
    pending_reversals: int = 0
    paper_runs_open: int = 0
    last_success_ts: int | None = None
    cycle_latencies_ms: list[float] = field(default_factory=list)
    last_heartbeat_ts: int = 0
    detector_diag: DetectorDiagnostics = field(default_factory=DetectorDiagnostics)

    def record_cycle(self, latency_ms: float, *, fetch_ok: int, fetch_failed: int) -> None:
        self.cycles += 1
        self.fetch_ok += fetch_ok
        self.fetch_failed += fetch_failed
        self.cycle_latencies_ms.append(latency_ms)
        if len(self.cycle_latencies_ms) > 1000:
            self.cycle_latencies_ms = self.cycle_latencies_ms[-1000:]
        if fetch_ok > 0:
            self.last_success_ts = int(time.time())

    def should_heartbeat(self, now: int, heartbeat_sec: int) -> bool:
        if self.last_heartbeat_ts == 0:
            return True
        return (now - self.last_heartbeat_ts) >= heartbeat_sec

    def render_heartbeat(
        self,
        *,
        universe: str,
        events_detected: int,
        pending_reversals: int,
        paper_runs_open: int,
    ) -> str:
        now = int(time.time())
        uptime = format_uptime(now - self.startup_ts)
        p50 = _percentile(self.cycle_latencies_ms, 50)
        p95 = _percentile(self.cycle_latencies_ms, 95)
        lines = [
            "[shock-paper] heartbeat",
            f"universe={universe}",
            f"uptime={uptime}",
            f"cycles={self.cycles}",
            f"fetch_ok={self.fetch_ok}",
            f"fetch_failed={self.fetch_failed}",
            f"events_detected={events_detected}",
            f"pending_reversals={pending_reversals}",
            f"paper_runs_open={paper_runs_open}",
            f"last_success_ts={self.last_success_ts or 'none'}",
            f"cycle_latency_ms_p50={p50:.1f}" if p50 is not None else "cycle_latency_ms_p50=none",
            f"cycle_latency_ms_p95={p95:.1f}" if p95 is not None else "cycle_latency_ms_p95=none",
        ]
        diag_lines = self.detector_diag.format_summary()
        if diag_lines:
            lines.append("detector_rejections:")
            lines.extend(diag_lines)
        return "\n".join(lines)

    def emit_heartbeat(self, text: str) -> None:
        print(text, flush=True)
        self.last_heartbeat_ts = int(time.time())
