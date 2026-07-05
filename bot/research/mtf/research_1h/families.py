"""1h entry families — YES/NO separate."""

from __future__ import annotations

from bot.research.mtf.research_1h.config import DEFAULT_THRESHOLDS, ENTRY_WINDOWS
from bot.research.mtf.research_1h.models import Obs1h


def _win(obs: Obs1h, name: str) -> bool:
    lo, hi = ENTRY_WINDOWS[name]
    return lo <= obs.entry_second <= hi


def _yes(obs: Obs1h) -> bool:
    return obs.yes_ask is not None and obs.yes_ask > 0


def _no(obs: Obs1h) -> bool:
    return obs.no_ask is not None and obs.no_ask > 0


def family_early_move_1m(obs, path, idx, th):
    if not _win(obs, "early_move_1m"):
        return None
    m = th.get("move_1m_usd", DEFAULT_THRESHOLDS["move_1m_usd"])
    if obs.btc_move_1m >= m and _yes(obs):
        return "YES"
    if obs.btc_move_1m <= -m and _no(obs):
        return "NO"
    return None


def family_early_move_5m(obs, path, idx, th):
    if not _win(obs, "early_move_5m"):
        return None
    m = th.get("move_5m_usd", DEFAULT_THRESHOLDS["move_5m_usd"])
    if obs.btc_move_5m >= m and obs.momentum_consistency >= 0.67 and _yes(obs):
        return "YES"
    if obs.btc_move_5m <= -m and obs.momentum_consistency >= 0.67 and _no(obs):
        return "NO"
    return None


def family_momentum_continue(obs, path, idx, th):
    if not _win(obs, "momentum_continue"):
        return None
    m = th.get("move_10m_usd", DEFAULT_THRESHOLDS["move_10m_usd"])
    if obs.btc_move_10m >= m and obs.btc_move_5m > 0 and _yes(obs):
        return "YES"
    if obs.btc_move_10m <= -m and obs.btc_move_5m < 0 and _no(obs):
        return "NO"
    return None


def family_vol_expansion(obs, path, idx, th):
    if not _win(obs, "vol_expansion"):
        return None
    ratio = th.get("vol_expansion_ratio", DEFAULT_THRESHOLDS["vol_expansion_ratio"])
    m = th.get("move_5m_usd", DEFAULT_THRESHOLDS["move_5m_usd"])
    if obs.vol_expansion >= ratio and obs.btc_move_5m >= m and _yes(obs):
        return "YES"
    if obs.vol_expansion >= ratio and obs.btc_move_5m <= -m and _no(obs):
        return "NO"
    return None


def family_failed_breakout(obs, path, idx, th):
    if not _win(obs, "failed_breakout") or idx < 2:
        return None
    m = th.get("failed_breakout_usd", DEFAULT_THRESHOLDS["failed_breakout_usd"])
    p1, p2 = path[idx - 2], path[idx - 1]
    if p1.btc_move_5m >= m and p2.btc_move_5m < m * 0.3 and obs.btc_move_1m < 0:
        return "NO" if _no(obs) else None
    if p1.btc_move_5m <= -m and p2.btc_move_5m > -m * 0.3 and obs.btc_move_1m > 0:
        return "YES" if _yes(obs) else None
    return None


def family_prob_lag(obs, path, idx, th):
    if not _win(obs, "prob_lag") or idx < 1 or obs.yes_mid is None:
        return None
    m = th.get("prob_lag_usd", DEFAULT_THRESHOLDS["prob_lag_usd"])
    prev = path[idx - 1]
    if obs.btc_move_5m >= m and prev.yes_mid and obs.yes_mid < prev.yes_mid + 0.02:
        return "YES" if _yes(obs) else None
    if obs.btc_move_5m <= -m and prev.yes_mid and obs.yes_mid > prev.yes_mid - 0.02:
        return "NO" if _no(obs) else None
    return None


def family_prob_divergence(obs, path, idx, th):
    if not _win(obs, "prob_divergence") or obs.yes_mid is None:
        return None
    div = th.get("divergence_prob", DEFAULT_THRESHOLDS["divergence_prob"])
    if obs.btc_move_15m > 0 and obs.yes_mid <= 0.5 - div:
        return "YES" if _yes(obs) else None
    if obs.btc_move_15m < 0 and obs.yes_mid >= 0.5 + div:
        return "NO" if _no(obs) else None
    return None


def family_late_time(obs, path, idx, th):
    if not _win(obs, "late_time_remaining"):
        return None
    m = th.get("move_15m_usd", DEFAULT_THRESHOLDS["move_15m_usd"])
    if obs.btc_move_15m >= m and _yes(obs):
        return "YES"
    if obs.btc_move_15m <= -m and _no(obs):
        return "NO"
    return None


FAMILIES = {
    "early_move_1m": family_early_move_1m,
    "early_move_5m": family_early_move_5m,
    "momentum_continue": family_momentum_continue,
    "vol_expansion": family_vol_expansion,
    "failed_breakout": family_failed_breakout,
    "prob_lag": family_prob_lag,
    "prob_divergence": family_prob_divergence,
    "late_time_remaining": family_late_time,
}


def calibrate_thresholds(train_obs: list[Obs1h]) -> dict[str, float]:
    if not train_obs:
        return dict(DEFAULT_THRESHOLDS)

    def pct(vals, q, default):
        s = sorted(abs(v) for v in vals if v)
        if not s:
            return default
        i = min(int(len(s) * q), len(s) - 1)
        return max(s[i], default * 0.5)

    return {
        "move_1m_usd": pct([o.btc_move_1m for o in train_obs], 0.65, DEFAULT_THRESHOLDS["move_1m_usd"]),
        "move_5m_usd": pct([o.btc_move_5m for o in train_obs], 0.65, DEFAULT_THRESHOLDS["move_5m_usd"]),
        "move_10m_usd": pct([o.btc_move_10m for o in train_obs], 0.65, DEFAULT_THRESHOLDS["move_10m_usd"]),
        "move_15m_usd": pct([o.btc_move_15m for o in train_obs], 0.65, DEFAULT_THRESHOLDS["move_15m_usd"]),
        "vol_expansion_ratio": DEFAULT_THRESHOLDS["vol_expansion_ratio"],
        "divergence_prob": DEFAULT_THRESHOLDS["divergence_prob"],
        "prob_lag_usd": pct([o.btc_move_5m for o in train_obs], 0.6, DEFAULT_THRESHOLDS["prob_lag_usd"]),
        "failed_breakout_usd": pct([o.btc_move_5m for o in train_obs], 0.75, DEFAULT_THRESHOLDS["failed_breakout_usd"]),
    }
