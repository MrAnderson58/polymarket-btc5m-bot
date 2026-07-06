"""Strategy definition for virtual forward replay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Strategy:
    direction: str
    max_entry: float
    min_delta: float | None = None
    max_delta: float | None = None
    max_spread: float = 0.02
    min_seconds_left: int = 60
    tp: float = 0.60
    name: str = ""

    def __post_init__(self) -> None:
        if self.direction not in ("YES", "NO"):
            raise ValueError(f"invalid direction: {self.direction}")

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        parts = [
            self.direction,
            f"entry<={self.max_entry:.2f}",
        ]
        if self.min_delta is not None:
            parts.append(f"delta>{self.min_delta:.0f}$")
        if self.max_delta is not None:
            parts.append(f"delta<{self.max_delta:.0f}$")
        parts.append(f"spread<{self.max_spread * 100:.0f}c")
        parts.append(f"sec>{self.min_seconds_left}")
        parts.append(f"tp={self.tp:.2f}")
        return " ".join(parts)

    def fingerprint(self) -> str:
        return "|".join([
            self.direction,
            f"{self.max_entry:.4f}",
            str(self.min_delta),
            str(self.max_delta),
            f"{self.max_spread:.4f}",
            str(self.min_seconds_left),
            f"{self.tp:.4f}",
        ])
