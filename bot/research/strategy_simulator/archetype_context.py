"""Precomputed archetype market context with causal features and exit outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.strategy_simulator.causal_features import CausalSnapshotFeatures, build_causal_features
from bot.research.strategy_simulator.exit_models import ExitSpec, build_exit_outcomes
from bot.research.strategy_simulator.market_context import _precompute_strikes


@dataclass
class ArchetypeMarketContext:
    market_slug: str
    observations: list[dict]
    timestamps: list[int]
    strikes: list[float | None]
    features_yes: list[CausalSnapshotFeatures | None]
    features_no: list[CausalSnapshotFeatures | None]
    exit_outcomes_yes: list[dict[str, tuple[float, int, bool]]]
    exit_outcomes_no: list[dict[str, tuple[float, int, bool]]]

    @classmethod
    def build(
        cls,
        market_slug: str,
        observations: list[dict],
        *,
        exit_specs: tuple[ExitSpec, ...],
    ) -> ArchetypeMarketContext:
        n = len(observations)
        timestamps = [int(o["timestamp"]) for o in observations]
        strikes = _precompute_strikes(observations)
        features_yes = [
            build_causal_features(observations, i, side="YES", timestamps=timestamps, strikes=strikes)
            for i in range(n)
        ]
        features_no = [
            build_causal_features(observations, i, side="NO", timestamps=timestamps, strikes=strikes)
            for i in range(n)
        ]
        exit_yes: list[dict[str, tuple[float, int, bool]]] = []
        exit_no: list[dict[str, tuple[float, int, bool]]] = []
        for i in range(n):
            ya = observations[i].get("yes_ask")
            na = observations[i].get("no_ask")
            entry_yes = float(ya) if ya is not None else 0.0
            entry_no = float(na) if na is not None else 0.0
            exit_yes.append(
                build_exit_outcomes(
                    observations, i, direction="YES", entry_price=entry_yes, specs=exit_specs,
                ),
            )
            exit_no.append(
                build_exit_outcomes(
                    observations, i, direction="NO", entry_price=entry_no, specs=exit_specs,
                ),
            )
        return cls(
            market_slug=market_slug,
            observations=observations,
            timestamps=timestamps,
            strikes=strikes,
            features_yes=features_yes,
            features_no=features_no,
            exit_outcomes_yes=exit_yes,
            exit_outcomes_no=exit_no,
        )


def build_archetype_contexts(
    paths: dict[str, list[dict]],
    *,
    exit_specs: tuple[ExitSpec, ...],
) -> dict[str, ArchetypeMarketContext]:
    return {
        slug: ArchetypeMarketContext.build(slug, path, exit_specs=exit_specs)
        for slug, path in paths.items()
    }
