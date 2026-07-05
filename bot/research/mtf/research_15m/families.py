"""15m entry signal families — YES and NO studied separately."""

from __future__ import annotations

from typing import Callable

from bot.research.mtf.research_15m.config import DEFAULT_MOVE_THRESHOLDS, ENTRY_WINDOWS
from bot.research.mtf.research_15m.models import Obs15m

FamilyFn = Callable[[Obs15m, list[Obs15m], int, dict[str, float]], str | None]


def _in_window(obs: Obs15m, name: str) -> bool:
    lo, hi = ENTRY_WINDOWS[name]
    return lo <= obs.entry_second <= hi


def _yes(obs: Obs15m) -> bool:
    return obs.yes_ask is not None and obs.yes_ask > 0


def _no(obs: Obs15m) -> bool:
    return obs.no_ask is not None and obs.no_ask > 0


def family_early_momentum(obs: Obs15m, path: list[Obs15m], idx: int, th: dict[str, float]) -> str | None:
    if not _in_window(obs, "early_momentum"):
        return None
    m = th.get("early_1m_usd", DEFAULT_MOVE_THRESHOLDS["early_1m_usd"])
    if obs.btc_move_1m >= m and obs.momentum_consistency >= 0.67 and _yes(obs):
        return "YES"
    if obs.btc_move_1m <= -m and obs.momentum_consistency >= 0.67 and _no(obs):
        return "NO"
    return None


def family_delayed_momentum(obs: Obs15m, path: list[Obs15m], idx: int, th: dict[str, float]) -> str | None:
    if not _in_window(obs, "delayed_momentum"):
        return None
    m = th.get("delayed_3m_usd", DEFAULT_MOVE_THRESHOLDS["delayed_3m_usd"])
    if obs.btc_move_3m >= m and obs.btc_move_1m > 0 and _yes(obs):
        return "YES"
    if obs.btc_move_3m <= -m and obs.btc_move_1m < 0 and _no(obs):
        return "NO"
    return None


def family_pullback_continuation(obs: Obs15m, path: list[Obs15m], idx: int, th: dict[str, float]) -> str | None:
    if not _in_window(obs, "pullback_continuation") or idx < 2:
        return None
    impulse = th.get("impulse_1m_usd", DEFAULT_MOVE_THRESHOLDS["impulse_1m_usd"])
    pb = th.get("pullback_pct", DEFAULT_MOVE_THRESHOLDS["pullback_pct"])
    prev2, prev1 = path[idx - 2], path[idx - 1]
    # YES: early up impulse, small pullback, resume up
    if prev2.btc_move_1m >= impulse and prev1.btc_move_30s < -pb * prev2.btc_move_1m and obs.btc_move_30s > 0:
        return "YES" if _yes(obs) else None
    if prev2.btc_move_1m <= -impulse and prev1.btc_move_30s > -pb * prev2.btc_move_1m and obs.btc_move_30s < 0:
        return "NO" if _no(obs) else None
    return None


def family_reversal_impulse(obs: Obs15m, path: list[Obs15m], idx: int, th: dict[str, float]) -> str | None:
    if not _in_window(obs, "reversal_impulse") or idx < 1:
        return None
    impulse = th.get("impulse_1m_usd", DEFAULT_MOVE_THRESHOLDS["impulse_1m_usd"])
    prev = path[idx - 1]
    if prev.btc_move_1m >= impulse and obs.btc_move_1m <= -impulse * 0.4:
        return "NO" if _no(obs) else None
    if prev.btc_move_1m <= -impulse and obs.btc_move_1m >= impulse * 0.4:
        return "YES" if _yes(obs) else None
    return None


def family_strike_recross(obs: Obs15m, path: list[Obs15m], idx: int, th: dict[str, float]) -> str | None:
    if not _in_window(obs, "strike_recross") or idx < 1 or obs.strike is None:
        return None
    band = th.get("recross_usd", DEFAULT_MOVE_THRESHOLDS["recross_usd"])
    prev = path[idx - 1]
    if prev.dist_strike_usd <= 0 and obs.dist_strike_usd > band:
        return "YES" if _yes(obs) else None
    if prev.dist_strike_usd >= 0 and obs.dist_strike_usd < -band:
        return "NO" if _no(obs) else None
    return None


def family_late_confirmation(obs: Obs15m, path: list[Obs15m], idx: int, th: dict[str, float]) -> str | None:
    if not _in_window(obs, "late_confirmation"):
        return None
    m = th.get("late_5m_usd", DEFAULT_MOVE_THRESHOLDS["late_5m_usd"])
    if obs.btc_move_5m >= m and obs.momentum_consistency >= 0.67 and _yes(obs):
        return "YES"
    if obs.btc_move_5m <= -m and obs.momentum_consistency >= 0.67 and _no(obs):
        return "NO"
    return None


def family_prob_spot_divergence(obs: Obs15m, path: list[Obs15m], idx: int, th: dict[str, float]) -> str | None:
    if not _in_window(obs, "prob_spot_divergence") or obs.yes_mid is None:
        return None
    div = th.get("divergence_prob", DEFAULT_MOVE_THRESHOLDS["divergence_prob"])
    spot_up = obs.btc_move_3m > 0
    prob_up = obs.yes_mid >= 0.5 + div
    prob_down = obs.yes_mid <= 0.5 - div
    if spot_up and prob_down and _yes(obs):
        return "YES"
    if not spot_up and obs.btc_move_3m < 0 and prob_up and _no(obs):
        return "NO"
    return None


FAMILIES: dict[str, FamilyFn] = {
    "early_momentum": family_early_momentum,
    "delayed_momentum": family_delayed_momentum,
    "pullback_continuation": family_pullback_continuation,
    "reversal_impulse": family_reversal_impulse,
    "strike_recross": family_strike_recross,
    "late_confirmation": family_late_confirmation,
    "prob_spot_divergence": family_prob_spot_divergence,
}


def calibrate_thresholds(train_obs: list[Obs15m]) -> dict[str, float]:
    """Calibrate move thresholds from train observations only."""
    if not train_obs:
        return dict(DEFAULT_MOVE_THRESHOLDS)

    def pct(vals: list[float], q: float, default: float) -> float:
        if not vals:
            return default
        s = sorted(abs(v) for v in vals)
        i = min(int(len(s) * q), len(s) - 1)
        return max(s[i], default * 0.5)

    return {
        "early_1m_usd": pct([o.btc_move_1m for o in train_obs], 0.65, DEFAULT_MOVE_THRESHOLDS["early_1m_usd"]),
        "delayed_3m_usd": pct([o.btc_move_3m for o in train_obs], 0.65, DEFAULT_MOVE_THRESHOLDS["delayed_3m_usd"]),
        "impulse_1m_usd": pct([o.btc_move_1m for o in train_obs], 0.75, DEFAULT_MOVE_THRESHOLDS["impulse_1m_usd"]),
        "pullback_pct": DEFAULT_MOVE_THRESHOLDS["pullback_pct"],
        "late_5m_usd": pct([o.btc_move_5m for o in train_obs], 0.65, DEFAULT_MOVE_THRESHOLDS["late_5m_usd"]),
        "divergence_prob": DEFAULT_MOVE_THRESHOLDS["divergence_prob"],
        "recross_usd": DEFAULT_MOVE_THRESHOLDS["recross_usd"],
    }
