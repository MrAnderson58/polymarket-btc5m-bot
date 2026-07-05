"""Daily entry families — session and structure aware."""

from __future__ import annotations

from bot.research.mtf.research_daily.config import DEFAULT_THRESHOLDS, ENTRY_WINDOWS
from bot.research.mtf.research_daily.models import ObsDaily


def _win(obs: ObsDaily, name: str) -> bool:
    lo, hi = ENTRY_WINDOWS[name]
    return lo <= obs.entry_second <= hi


def _yes(obs): return obs.yes_ask and obs.yes_ask > 0
def _no(obs): return obs.no_ask and obs.no_ask > 0


def _session_family(session: str):
    def fn(obs, path, idx, th):
        key = f"session_{session}"
        if not _win(obs, key) or obs.session != session:
            return None
        m = th.get("intraday_trend_usd", DEFAULT_THRESHOLDS["intraday_trend_usd"])
        if obs.intraday_trend_usd >= m and _yes(obs):
            return "YES"
        if obs.intraday_trend_usd <= -m and _no(obs):
            return "NO"
        return None
    return fn


def family_open_distance(obs, path, idx, th):
    if not _win(obs, "open_distance"):
        return None
    p = th.get("open_dist_pct", DEFAULT_THRESHOLDS["open_dist_pct"])
    if obs.dist_daily_open_pct >= p and _yes(obs):
        return "YES"
    if obs.dist_daily_open_pct <= -p and _no(obs):
        return "NO"
    return None


def family_rolling_extreme(obs, path, idx, th):
    if not _win(obs, "rolling_extreme"):
        return None
    p = th.get("high_low_dist_pct", DEFAULT_THRESHOLDS["high_low_dist_pct"])
    if obs.dist_from_low_pct >= -p and obs.dist_from_low_pct <= 0 and obs.intraday_trend_usd > 0 and _yes(obs):
        return "YES"
    if obs.dist_from_high_pct <= p and obs.dist_from_high_pct >= 0 and obs.intraday_trend_usd < 0 and _no(obs):
        return "NO"
    return None


def family_vol_regime(obs, path, idx, th):
    if not _win(obs, "vol_regime") or obs.vol_regime != "high":
        return None
    m = th.get("displacement_usd", DEFAULT_THRESHOLDS["displacement_usd"])
    if obs.intraday_trend_usd >= m and _yes(obs):
        return "YES"
    if obs.intraday_trend_usd <= -m and _no(obs):
        return "NO"
    return None


def family_intraday_trend(obs, path, idx, th):
    if not _win(obs, "intraday_trend"):
        return None
    m = th.get("intraday_trend_usd", DEFAULT_THRESHOLDS["intraday_trend_usd"])
    if obs.intraday_trend_usd >= m and (obs.btc_trend_1h or 0) > 0 and _yes(obs):
        return "YES"
    if obs.intraday_trend_usd <= -m and (obs.btc_trend_1h or 0) < 0 and _no(obs):
        return "NO"
    return None


def family_reversal_displacement(obs, path, idx, th):
    if not _win(obs, "reversal_displacement") or idx < 2:
        return None
    m = th.get("displacement_usd", DEFAULT_THRESHOLDS["displacement_usd"])
    r = th.get("reversal_usd", DEFAULT_THRESHOLDS["reversal_usd"])
    p2, p1 = path[idx - 2], path[idx - 1]
    if p2.intraday_trend_usd >= m and p1.intraday_trend_usd < r and obs.intraday_trend_usd < 0:
        return "NO" if _no(obs) else None
    if p2.intraday_trend_usd <= -m and p1.intraday_trend_usd > -r and obs.intraday_trend_usd > 0:
        return "YES" if _yes(obs) else None
    return None


def family_prob_divergence(obs, path, idx, th):
    if not _win(obs, "prob_divergence") or obs.yes_mid is None:
        return None
    div = th.get("divergence_prob", DEFAULT_THRESHOLDS["divergence_prob"])
    if obs.intraday_trend_usd > 0 and obs.yes_mid <= 0.5 - div:
        return "YES" if _yes(obs) else None
    if obs.intraday_trend_usd < 0 and obs.yes_mid >= 0.5 + div:
        return "NO" if _no(obs) else None
    return None


def family_time_to_expiry(obs, path, idx, th):
    if not _win(obs, "time_to_expiry") or obs.seconds_left is None:
        return None
    m = th.get("intraday_trend_usd", DEFAULT_THRESHOLDS["intraday_trend_usd"]) * 0.5
    if obs.intraday_trend_usd >= m and _yes(obs):
        return "YES"
    if obs.intraday_trend_usd <= -m and _no(obs):
        return "NO"
    return None


FAMILIES = {
    "session_asia": _session_family("asia"),
    "session_europe": _session_family("europe"),
    "session_us": _session_family("us"),
    "open_distance": family_open_distance,
    "rolling_extreme": family_rolling_extreme,
    "vol_regime": family_vol_regime,
    "intraday_trend": family_intraday_trend,
    "reversal_displacement": family_reversal_displacement,
    "prob_divergence": family_prob_divergence,
    "time_to_expiry": family_time_to_expiry,
}


def calibrate_thresholds(train_obs: list[ObsDaily]) -> dict[str, float]:
    if not train_obs:
        return dict(DEFAULT_THRESHOLDS)

    def pct(vals, q, default):
        s = sorted(abs(v) for v in vals if v)
        if not s:
            return default
        i = min(int(len(s) * q), len(s) - 1)
        return max(s[i], default * 0.5)

    return {
        "displacement_usd": pct([o.intraday_trend_usd for o in train_obs], 0.75, DEFAULT_THRESHOLDS["displacement_usd"]),
        "reversal_usd": pct([o.intraday_trend_usd for o in train_obs], 0.5, DEFAULT_THRESHOLDS["reversal_usd"]),
        "divergence_prob": DEFAULT_THRESHOLDS["divergence_prob"],
        "open_dist_pct": DEFAULT_THRESHOLDS["open_dist_pct"],
        "high_low_dist_pct": DEFAULT_THRESHOLDS["high_low_dist_pct"],
        "vol_regime_ratio": DEFAULT_THRESHOLDS["vol_regime_ratio"],
        "intraday_trend_usd": pct([o.intraday_trend_usd for o in train_obs], 0.65, DEFAULT_THRESHOLDS["intraday_trend_usd"]),
    }
