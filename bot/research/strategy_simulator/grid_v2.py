"""Discovery v2 grid generation — diverse archetypes with symmetric YES/NO."""

from __future__ import annotations

from itertools import product

from bot.research.strategy_simulator.archetypes import ArchetypeStrategy
from bot.research.strategy_simulator.exit_models import ExitModel, ExitSpec


V2_MAX_ENTRIES = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45)
V2_MAX_SPREADS = (0.01, 0.02, 0.03, 0.05)
V2_MIN_SECONDS = (45, 60, 90, 120)
V2_MAX_SECONDS_LATE = (30, 45, 60)
V2_TPS = (0.55, 0.60, 0.65)
V2_TIME_EXITS = (30, 60, 90)
V2_TRAILING_STOPS = (0.05, 0.08)
V2_DIRECTIONS = ("YES", "NO")


def _exit_specs_for_grid() -> tuple[ExitSpec, ...]:
    specs: list[ExitSpec] = []
    for tp in V2_TPS:
        specs.append(ExitSpec(model=ExitModel.FIXED_TP, tp=tp))
    for sec in V2_TIME_EXITS:
        specs.append(ExitSpec(model=ExitModel.TIME_EXIT, time_exit_seconds=sec))
    specs.append(ExitSpec(model=ExitModel.SETTLEMENT))
    for trail in V2_TRAILING_STOPS:
        specs.append(ExitSpec(model=ExitModel.TRAILING, trailing_stop=trail))
    return tuple(specs)


def _momentum_grid() -> list[ArchetypeStrategy]:
    strategies: list[ArchetypeStrategy] = []
    for direction, max_entry, max_spread, min_sec, min_vel, vel_win, confirm, exit_spec in product(
        V2_DIRECTIONS,
        V2_MAX_ENTRIES,
        V2_MAX_SPREADS,
        V2_MIN_SECONDS,
        (0.5, 1.0, 2.0),
        (5, 10),
        (0, 1),
        [s for s in _exit_specs_for_grid() if s.model in (ExitModel.FIXED_TP, ExitModel.TIME_EXIT)],
    ):
        if exit_spec.tp is not None and exit_spec.tp <= max_entry:
            continue
        strategies.append(ArchetypeStrategy(
            archetype="momentum",
            direction=direction,
            exit_spec=exit_spec,
            max_entry=max_entry,
            max_spread=max_spread,
            min_seconds_left=min_sec,
            min_velocity=min_vel,
            velocity_window=vel_win,
            min_confirmations=confirm,
        ))
    return strategies


def _mean_reversion_grid() -> list[ArchetypeStrategy]:
    strategies: list[ArchetypeStrategy] = []
    for direction, max_entry, max_spread, min_sec, min_abs, vel_win, exit_spec in product(
        V2_DIRECTIONS,
        V2_MAX_ENTRIES,
        V2_MAX_SPREADS,
        V2_MIN_SECONDS,
        (25.0, 50.0, 75.0),
        (5, 10),
        _exit_specs_for_grid(),
    ):
        if exit_spec.tp is not None and exit_spec.tp <= max_entry:
            continue
        strategies.append(ArchetypeStrategy(
            archetype="mean_reversion",
            direction=direction,
            exit_spec=exit_spec,
            max_entry=max_entry,
            max_spread=max_spread,
            min_seconds_left=min_sec,
            min_abs_delta=min_abs,
            velocity_window=vel_win,
        ))
    return strategies


def _late_convergence_grid() -> list[ArchetypeStrategy]:
    strategies: list[ArchetypeStrategy] = []
    for direction, max_entry, max_spread, max_sec, min_abs, max_norm, exit_spec in product(
        V2_DIRECTIONS,
        V2_MAX_ENTRIES,
        V2_MAX_SPREADS,
        V2_MAX_SECONDS_LATE,
        (5.0, 15.0, 25.0),
        (1.0, 2.0, 3.0),
        [s for s in _exit_specs_for_grid() if s.model != ExitModel.TRAILING],
    ):
        if exit_spec.tp is not None and exit_spec.tp <= max_entry:
            continue
        strategies.append(ArchetypeStrategy(
            archetype="late_convergence",
            direction=direction,
            exit_spec=exit_spec,
            max_entry=max_entry,
            max_spread=max_spread,
            max_seconds_left=max_sec,
            min_abs_delta=min_abs,
            max_normalized_distance=max_norm,
        ))
    return strategies


def _spread_dislocation_grid() -> list[ArchetypeStrategy]:
    strategies: list[ArchetypeStrategy] = []
    for direction, max_entry, max_spread, min_sec, min_spread, min_gap, exit_spec in product(
        V2_DIRECTIONS,
        V2_MAX_ENTRIES,
        V2_MAX_SPREADS,
        V2_MIN_SECONDS,
        (0.02, 0.03),
        (0.03, 0.05),
        [s for s in _exit_specs_for_grid() if s.model in (ExitModel.FIXED_TP, ExitModel.TIME_EXIT)],
    ):
        if exit_spec.tp is not None and exit_spec.tp <= max_entry:
            continue
        if min_spread > max_spread:
            continue
        strategies.append(ArchetypeStrategy(
            archetype="spread_dislocation",
            direction=direction,
            exit_spec=exit_spec,
            max_entry=max_entry,
            max_spread=max_spread,
            min_seconds_left=min_sec,
            min_spread=min_spread,
            min_spread_change=0.005,
            min_complement_gap=min_gap,
        ))
    return strategies


def _legacy_cheap_grid() -> list[ArchetypeStrategy]:
    """Smaller legacy grid for baseline comparison (symmetric YES/NO)."""
    strategies: list[ArchetypeStrategy] = []
    yes_deltas = ((0, None), (25, None), (0, 50))
    no_deltas = ((None, 0), (None, -25), (-50, 0))
    for direction, max_entry, max_spread, min_sec, exit_spec in product(
        V2_DIRECTIONS,
        (0.20, 0.25, 0.30),
        (0.01, 0.02, 0.03),
        (45, 60, 90),
        [s for s in _exit_specs_for_grid() if s.model == ExitModel.FIXED_TP],
    ):
        if exit_spec.tp is not None and exit_spec.tp <= max_entry:
            continue
        delta_pairs = yes_deltas if direction == "YES" else no_deltas
        for min_d, max_d in delta_pairs:
            strategies.append(ArchetypeStrategy(
                archetype="legacy_cheap",
                direction=direction,
                exit_spec=exit_spec,
                max_entry=max_entry,
                max_spread=max_spread,
                min_seconds_left=min_sec,
                min_delta=float(min_d) if min_d is not None else None,
                max_delta=float(max_d) if max_d is not None else None,
            ))
    return strategies


def generate_discovery_v2_grid(
    *,
    include_legacy: bool = True,
    archetypes: tuple[str, ...] | None = None,
) -> list[ArchetypeStrategy]:
    """Generate full v2 discovery grid across archetype families."""
    builders = {
        "momentum": _momentum_grid,
        "mean_reversion": _mean_reversion_grid,
        "late_convergence": _late_convergence_grid,
        "spread_dislocation": _spread_dislocation_grid,
        "legacy_cheap": _legacy_cheap_grid,
    }
    selected = archetypes or tuple(builders.keys())
    grid: list[ArchetypeStrategy] = []
    for name in selected:
        if name == "legacy_cheap" and not include_legacy:
            continue
        fn = builders.get(name)
        if fn:
            grid.extend(fn())
    return grid


def grid_exit_specs() -> tuple[ExitSpec, ...]:
    return _exit_specs_for_grid()


def grid_summary() -> dict[str, int]:
    grid = generate_discovery_v2_grid()
    by_arch: dict[str, int] = {}
    by_dir: dict[str, int] = {"YES": 0, "NO": 0}
    for s in grid:
        by_arch[s.archetype] = by_arch.get(s.archetype, 0) + 1
        by_dir[s.direction] += 1
    return {"total": len(grid), "by_archetype": by_arch, "by_direction": by_dir}
