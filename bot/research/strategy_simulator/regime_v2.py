"""Regime diagnostics v2 — compare TRAIN vs VALIDATION signal distributions."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median

from bot.research.strategy_simulator.data_quality import _percentile
from bot.research.strategy_simulator.regime import RegimeDiagnostics, analyze_regime


@dataclass
class SignalDistribution:
    name: str
    entry_ask_p25: float = 0.0
    entry_ask_p50: float = 0.0
    entry_ask_p75: float = 0.0
    abs_delta_p50: float = 0.0
    spread_p50: float = 0.0
    seconds_left_p50: float = 0.0
    opportunity_rate: float = 0.0
    sample_count: int = 0


@dataclass
class RegimeShiftAssessment:
    btc_delta_shift: str = ""
    ask_shift: str = ""
    spread_shift: str = ""
    seconds_left_shift: str = ""
    likely_causes: list[str] = field(default_factory=list)


@dataclass
class RegimeComparison:
    train: RegimeDiagnostics
    validation: RegimeDiagnostics
    test: RegimeDiagnostics
    assessment: RegimeShiftAssessment


def _signal_distribution(name: str, paths: dict[str, list[dict]]) -> SignalDistribution:
    d = SignalDistribution(name=name)
    asks: list[float] = []
    abs_deltas: list[float] = []
    spreads: list[float] = []
    seconds: list[float] = []
    markets_with_entry = 0

    for path in paths.values():
        has_cheap = False
        for obs in path:
            ya = obs.get("yes_ask")
            if ya is not None and float(ya) <= 0.30:
                has_cheap = True
            delta = obs.get("delta")
            if delta is None:
                strike = obs.get("strike")
                if strike is not None:
                    delta = float(obs["btc_price"]) - float(strike)
            if delta is not None:
                abs_deltas.append(abs(float(delta)))
            sl = obs.get("seconds_left")
            if sl is not None:
                seconds.append(float(sl))
            yb, yask = obs.get("yes_bid"), obs.get("yes_ask")
            if yb is not None and yask is not None:
                spreads.append(max(0.0, float(yask) - float(yb)))
                if ya is not None:
                    asks.append(float(ya))
        if has_cheap:
            markets_with_entry += 1

    if asks:
        d.entry_ask_p25 = _percentile(asks, 0.25)
        d.entry_ask_p50 = _percentile(asks, 0.50)
        d.entry_ask_p75 = _percentile(asks, 0.75)
    if abs_deltas:
        d.abs_delta_p50 = _percentile(abs_deltas, 0.50)
    if spreads:
        d.spread_p50 = _percentile(spreads, 0.50)
    if seconds:
        d.seconds_left_p50 = _percentile(seconds, 0.50)
    d.sample_count = sum(len(p) for p in paths.values())
    d.opportunity_rate = markets_with_entry / len(paths) if paths else 0.0
    return d


def _shift_label(train_val: float, val_val: float, *, pct_threshold: float = 0.15) -> str:
    if train_val == 0:
        return "unknown"
    rel = (val_val - train_val) / abs(train_val)
    if rel > pct_threshold:
        return "validation_higher"
    if rel < -pct_threshold:
        return "validation_lower"
    return "stable"


def assess_regime_shift(
    train: RegimeDiagnostics,
    validation: RegimeDiagnostics,
    train_sig: SignalDistribution,
    val_sig: SignalDistribution,
) -> RegimeShiftAssessment:
    a = RegimeShiftAssessment()
    a.btc_delta_shift = _shift_label(
        train.median_abs_final_delta, validation.median_abs_final_delta,
    )
    a.ask_shift = _shift_label(train_sig.entry_ask_p50, val_sig.entry_ask_p50)
    a.spread_shift = _shift_label(train.median_spread, validation.median_spread)
    a.seconds_left_shift = _shift_label(train_sig.seconds_left_p50, val_sig.seconds_left_p50)

    causes: list[str] = []
    if a.btc_delta_shift == "validation_lower":
        causes.append("regime_shift: lower BTC volatility/move in validation")
    if a.ask_shift == "validation_higher":
        causes.append("regime_shift: cheaper entries scarcer in validation")
    if val_sig.opportunity_rate < train_sig.opportunity_rate * 0.8:
        causes.append("sparse_signal: opportunity rate dropped in validation")
    if not causes:
        causes.append("overfitting: stable regime but train EV does not transfer")
    causes.append("exit_model: fixed TP=0.55 may not generalize across bid dynamics")
    a.likely_causes = causes
    return a


def compare_regime_splits(
    train_paths: dict[str, list[dict]],
    val_paths: dict[str, list[dict]],
    test_paths: dict[str, list[dict]],
) -> RegimeComparison:
    train_r = analyze_regime("train", train_paths)
    val_r = analyze_regime("validation", val_paths)
    test_r = analyze_regime("test", test_paths)
    train_sig = _signal_distribution("train", train_paths)
    val_sig = _signal_distribution("validation", val_paths)
    assessment = assess_regime_shift(train_r, val_r, train_sig, val_sig)
    return RegimeComparison(
        train=train_r,
        validation=val_r,
        test=test_r,
        assessment=assessment,
    )
