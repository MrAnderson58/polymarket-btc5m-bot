"""Performance timing utilities."""

from __future__ import annotations

import io
import pstats
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class PerfReport:
    steps: list[tuple[str, float]] = field(default_factory=list)
    _wall_start: float = field(default_factory=time.perf_counter)
    _profiler: object | None = field(default=None, repr=False)

    def start_wall(self) -> None:
        self._wall_start = time.perf_counter()

    def enable_cprofile(self) -> None:
        import cProfile
        import sys

        if self._profiler is not None:
            return
        if sys.getprofile() is not None:
            return
        self._profiler = cProfile.Profile()
        self._profiler.enable()

    def stop_cprofile(self) -> None:
        if self._profiler is not None:
            self._profiler.disable()

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.steps.append((name, time.perf_counter() - start))

    def add(self, name: str, seconds: float) -> None:
        self.steps.append((name, seconds))

    def total(self) -> float:
        return sum(sec for _, sec in self.steps)

    def wall_total(self) -> float:
        return time.perf_counter() - self._wall_start

    def untimed(self) -> float:
        return max(0.0, self.wall_total() - self.total())

    def as_dict(self) -> dict:
        return {
            "steps": {name: round(sec, 3) for name, sec in self.steps},
            "total_sec": round(self.total(), 3),
            "wall_sec": round(self.wall_total(), 3),
            "untimed_sec": round(self.untimed(), 3),
        }

    def print_top_functions(self, *, limit: int = 20, file=None) -> None:
        if self._profiler is None:
            return
        self.stop_cprofile()
        file = file or sys.stdout
        stream = io.StringIO()
        stats = pstats.Stats(self._profiler, stream=stream)
        stats.sort_stats("cumulative")
        stats.print_stats(limit)
        print(f"\n--- Top {limit} functions (cumulative) ---", file=file)
        print(stream.getvalue(), file=file)

    def print_report(self, *, file=None, show_top: bool = True) -> None:
        file = file or sys.stdout
        print("\n--- Performance ---", file=file)
        for name, sec in self.steps:
            print(f"{name}: {sec:.2f} s", file=file)
        print(f"TOTAL: {self.total():.2f} s", file=file)
        wall = self.wall_total()
        untimed = self.untimed()
        if untimed > 0.05:
            print(f"Untimed: {untimed:.2f} s", file=file)
        print(f"Wall clock: {wall:.2f} s", file=file)
        if show_top:
            self.print_top_functions(file=file)
