"""Strategy archetype definitions for Discovery v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json

from bot.research.strategy_simulator.causal_features import CausalSnapshotFeatures
from bot.research.strategy_simulator.exit_models import ExitModel, ExitSpec


ARCHETYPE_NAMES = (
    "momentum",
    "mean_reversion",
    "late_convergence",
    "spread_dislocation",
    "legacy_cheap",
)


@dataclass(frozen=True)
class ArchetypeStrategy:
    archetype: str
    direction: str
    exit_spec: ExitSpec
    max_entry: float
    max_spread: float
    min_seconds_left: int | None = None
    max_seconds_left: int | None = None
    min_delta: float | None = None
    max_delta: float | None = None
    min_abs_delta: float | None = None
    min_velocity: float | None = None
    velocity_window: int = 10
    min_confirmations: int = 0
    min_complement_gap: float | None = None
    min_spread: float | None = None
    min_spread_change: float | None = None
    max_normalized_distance: float | None = None
    name: str = ""

    def __post_init__(self) -> None:
        if self.direction not in ("YES", "NO"):
            raise ValueError(f"invalid direction: {self.direction}")
        if self.archetype not in ARCHETYPE_NAMES:
            raise ValueError(f"unknown archetype: {self.archetype}")

    @property
    def family_key(self) -> str:
        return f"{self.archetype}|{self.direction}|{self.exit_spec.model.value}"

    def fingerprint(self) -> str:
        payload = asdict(self)
        payload["exit_spec"] = {
            "model": self.exit_spec.model.value,
            "tp": self.exit_spec.tp,
            "time_exit_seconds": self.exit_spec.time_exit_seconds,
            "trailing_stop": self.exit_spec.trailing_stop,
        }
        return json.dumps(payload, sort_keys=True)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        parts = [
            self.archetype,
            self.direction,
            self.exit_spec.label(),
            f"ask<={self.max_entry:.2f}",
            f"spread<={self.max_spread:.3f}",
        ]
        if self.min_seconds_left is not None:
            parts.append(f"sec>={self.min_seconds_left}")
        if self.max_seconds_left is not None:
            parts.append(f"sec<={self.max_seconds_left}")
        if self.min_delta is not None:
            parts.append(f"d>={self.min_delta:.0f}")
        if self.max_delta is not None:
            parts.append(f"d<={self.max_delta:.0f}")
        if self.min_abs_delta is not None:
            parts.append(f"|d|>={self.min_abs_delta:.0f}")
        if self.min_velocity is not None:
            parts.append(f"vel>={self.min_velocity:.1f}@{self.velocity_window}s")
        if self.min_confirmations:
            parts.append(f"confirm>={self.min_confirmations}")
        return " | ".join(parts)

    def predicate_lines(self) -> list[str]:
        return self.label.split(" | ")


def _entry_ask(feat: CausalSnapshotFeatures, direction: str) -> float | None:
    return feat.yes_ask if direction == "YES" else feat.no_ask


def _base_filters(feat: CausalSnapshotFeatures, strategy: ArchetypeStrategy) -> bool:
    if strategy.min_seconds_left is not None and feat.seconds_left < strategy.min_seconds_left:
        return False
    if strategy.max_seconds_left is not None and feat.seconds_left > strategy.max_seconds_left:
        return False
    ask = _entry_ask(feat, strategy.direction)
    if ask is None or ask <= 0.05 or ask > strategy.max_entry:
        return False
    if feat.spread_now is None or feat.spread_now > strategy.max_spread:
        return False
    if feat.btc_delta is None:
        return False
    if strategy.min_delta is not None and feat.btc_delta < strategy.min_delta:
        return False
    if strategy.max_delta is not None and feat.btc_delta > strategy.max_delta:
        return False
    if strategy.min_abs_delta is not None and abs(feat.btc_delta) < strategy.min_abs_delta:
        return False
    if strategy.exit_spec.model == ExitModel.FIXED_TP and strategy.exit_spec.tp is not None:
        if ask >= strategy.exit_spec.tp:
            return False
    return True


def _velocity_at(feat: CausalSnapshotFeatures, window: int) -> float | None:
    if window == 5:
        return feat.btc_velocity_5s
    if window == 10:
        return feat.btc_velocity_10s
    if window == 20:
        return feat.btc_velocity_20s
    if window == 30:
        return feat.btc_velocity_30s
    return feat.btc_velocity_10s


def matches_archetype(feat: CausalSnapshotFeatures, strategy: ArchetypeStrategy) -> bool:
    """Exact entry predicate for archetype strategies."""
    if not _base_filters(feat, strategy):
        return False

    if strategy.archetype == "momentum":
        vel = _velocity_at(feat, strategy.velocity_window)
        if vel is None:
            return False
        if strategy.direction == "YES":
            if feat.btc_delta < 0:
                return False
            if strategy.min_velocity is not None and vel < strategy.min_velocity:
                return False
        else:
            if feat.btc_delta > 0:
                return False
            if strategy.min_velocity is not None and vel > -strategy.min_velocity:
                return False
        if strategy.min_confirmations and feat.prior_aligned_snapshots < strategy.min_confirmations:
            return False

    elif strategy.archetype == "mean_reversion":
        vel = _velocity_at(feat, strategy.velocity_window)
        if vel is None:
            return False
        if strategy.direction == "YES":
            if feat.btc_delta >= 0:
                return False
            if vel <= 0:
                return False
        else:
            if feat.btc_delta <= 0:
                return False
            if vel >= 0:
                return False

    elif strategy.archetype == "late_convergence":
        if strategy.max_normalized_distance is not None:
            if feat.normalized_distance is None:
                return False
            if feat.normalized_distance > strategy.max_normalized_distance:
                return False
        ask = _entry_ask(feat, strategy.direction)
        assert ask is not None
        if strategy.direction == "YES" and feat.btc_delta is not None and feat.btc_delta > 0:
            if ask > 0.55:
                return False
        if strategy.direction == "NO" and feat.btc_delta is not None and feat.btc_delta < 0:
            if ask > 0.55:
                return False

    elif strategy.archetype == "spread_dislocation":
        if strategy.min_spread is not None:
            if feat.spread_now is None or feat.spread_now < strategy.min_spread:
                return False
        if strategy.min_spread_change is not None:
            if feat.spread_change is None or feat.spread_change < strategy.min_spread_change:
                return False
        if strategy.min_complement_gap is not None:
            if feat.complement_gap is None or feat.complement_gap < strategy.min_complement_gap:
                return False

    elif strategy.archetype == "legacy_cheap":
        if strategy.direction == "YES":
            if strategy.min_delta is not None and feat.btc_delta < strategy.min_delta:
                return False
            if strategy.max_delta is not None and feat.btc_delta > strategy.max_delta:
                return False
        else:
            if strategy.min_delta is not None and feat.btc_delta < strategy.min_delta:
                return False
            if strategy.max_delta is not None and feat.btc_delta > strategy.max_delta:
                return False

    return True
