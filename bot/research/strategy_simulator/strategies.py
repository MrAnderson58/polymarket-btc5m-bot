"""Strategy definition for virtual forward replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json


class StrategyValidationError(ValueError):
    """Raised when strategy parameters form an invalid or empty filter."""


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
        self.validate()

    def validate(self) -> None:
        """Raise StrategyValidationError if parameters are invalid."""
        if self.max_entry <= 0 or self.tp <= self.max_entry:
            raise StrategyValidationError("tp must be greater than max_entry")
        if self.min_delta is not None and self.max_delta is not None:
            if self.min_delta >= self.max_delta:
                raise StrategyValidationError(
                    f"empty delta interval: min_delta={self.min_delta} "
                    f"max_delta={self.max_delta}"
                )
        if self.direction == "YES":
            if self.min_delta is not None and self.min_delta < 0:
                raise StrategyValidationError("YES min_delta must be >= 0 or None")
            if self.max_delta is not None and self.max_delta < 0:
                raise StrategyValidationError("YES max_delta must be >= 0 or None")
        if self.direction == "NO":
            if self.min_delta is not None and self.min_delta > 0:
                raise StrategyValidationError("NO min_delta must be <= 0 or None")
            if self.max_delta is not None and self.max_delta > 0:
                raise StrategyValidationError("NO max_delta must be <= 0 or None")

    @classmethod
    def is_valid(
        cls,
        *,
        direction: str,
        max_entry: float,
        min_delta: float | None,
        max_delta: float | None,
        max_spread: float,
        min_seconds_left: int,
        tp: float,
    ) -> bool:
        try:
            cls(
                direction=direction,
                max_entry=max_entry,
                min_delta=min_delta,
                max_delta=max_delta,
                max_spread=max_spread,
                min_seconds_left=min_seconds_left,
                tp=tp,
            )
        except (StrategyValidationError, ValueError):
            return False
        return True

    def matches_btc_delta(self, btc_delta: float) -> bool:
        """Exact predicate used by the simulator."""
        if self.min_delta is not None and btc_delta < self.min_delta:
            return False
        if self.max_delta is not None and btc_delta > self.max_delta:
            return False
        return True

    def predicate_lines(self) -> list[str]:
        """Exact predicates used by _matches_strategy (for reports)."""
        lines = [
            self.direction,
            f"ask <= {self.max_entry:.2f}",
            f"seconds_left >= {self.min_seconds_left}",
            f"spread <= {self.max_spread:.4f}",
            f"ask < tp ({self.tp:.2f})",
        ]
        if self.min_delta is not None:
            lines.append(f"btc_delta >= {self.min_delta:.2f}")
        if self.max_delta is not None:
            lines.append(f"btc_delta <= {self.max_delta:.2f}")
        return lines

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        return " | ".join(self.predicate_lines())

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

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> Strategy:
        return cls(**json.loads(raw))
