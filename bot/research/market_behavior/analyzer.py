"""Per-market statistics from v4_shadow_observations paths."""

from __future__ import annotations

from bot.research.market_behavior.buckets import entry_price_bucket
from bot.research.market_behavior.config import (
    LATE_WINDOW_BUCKETS,
    MIN_SECONDS_LEFT_FOR_TP,
    TP_LEVELS,
)
from bot.research.market_behavior.models import (
    LateWindowSnapshot,
    MarketSummary,
    TpAggregate,
    TpSample,
)


def _min_max(values: list[float | None]) -> tuple[float | None, float | None]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None, None
    return min(clean), max(clean)


def _resolve_strike(observations: list[dict]) -> float | None:
    for obs in reversed(observations):
        strike = obs.get("strike")
        if strike is not None:
            return float(strike)
    return None


def _winning_side(strike: float | None, final_btc: float) -> str:
    if strike is None:
        return "UNKNOWN"
    if final_btc > strike:
        return "YES"
    if final_btc < strike:
        return "NO"
    return "TIE"


def _window_start_ts(observations: list[dict]) -> int:
    if observations[0].get("window_start_ts") is not None:
        return int(observations[0]["window_start_ts"])
    return int(observations[0]["timestamp"]) - int(observations[0].get("seconds_from_start", 0))


def _obs_closest_to_seconds_left(
    observations: list[dict],
    target: int,
) -> dict | None:
    best: dict | None = None
    best_diff = 10**9
    for obs in observations:
        sl = obs.get("seconds_left")
        if sl is None:
            continue
        diff = abs(int(sl) - target)
        if diff < best_diff:
            best_diff = diff
            best = obs
    return best if best_diff <= 8 else None


def analyze_market_summary(market_slug: str, observations: list[dict]) -> MarketSummary:
    strike = _resolve_strike(observations)
    final = observations[-1]
    final_btc = float(final["btc_price"])
    delta_final = (final_btc - strike) if strike is not None else None

    yes_bids = [obs.get("yes_bid") for obs in observations]
    yes_asks = [obs.get("yes_ask") for obs in observations]
    no_bids = [obs.get("no_bid") for obs in observations]
    no_asks = [obs.get("no_ask") for obs in observations]

    yb_min, yb_max = _min_max(yes_bids)
    ya_min, ya_max = _min_max(yes_asks)
    nb_min, nb_max = _min_max(no_bids)
    na_min, na_max = _min_max(no_asks)

    return MarketSummary(
        market_slug=market_slug,
        window_start_ts=_window_start_ts(observations),
        strike=strike,
        final_btc=final_btc,
        btc_delta_final=delta_final,
        winning_side=_winning_side(strike, final_btc),
        yes_bid_min=yb_min,
        yes_bid_max=yb_max,
        yes_ask_min=ya_min,
        yes_ask_max=ya_max,
        no_bid_min=nb_min,
        no_bid_max=nb_max,
        no_ask_min=na_min,
        no_ask_max=na_max,
        observation_count=len(observations),
    )


def analyze_late_windows(market_slug: str, observations: list[dict]) -> list[LateWindowSnapshot]:
    if not observations:
        return []
    strike = _resolve_strike(observations)
    close_btc = float(observations[-1]["btc_price"])
    close_yes_ask = observations[-1].get("yes_ask")
    close_no_ask = observations[-1].get("no_ask")

    out: list[LateWindowSnapshot] = []
    for bucket in LATE_WINDOW_BUCKETS:
        obs = _obs_closest_to_seconds_left(observations, bucket)
        if obs is None:
            out.append(LateWindowSnapshot(
                market_slug=market_slug,
                seconds_bucket=bucket,
                obs_timestamp=None,
                seconds_left_actual=None,
                btc_price=None,
                btc_delta_vs_strike=None,
                yes_bid=None,
                yes_ask=None,
                no_bid=None,
                no_ask=None,
                btc_move_to_close=None,
                yes_ask_move_to_close=None,
                no_ask_move_to_close=None,
            ))
            continue

        btc = float(obs["btc_price"])
        delta = (btc - strike) if strike is not None else None
        yes_ask = obs.get("yes_ask")
        no_ask = obs.get("no_ask")
        out.append(LateWindowSnapshot(
            market_slug=market_slug,
            seconds_bucket=bucket,
            obs_timestamp=int(obs["timestamp"]),
            seconds_left_actual=int(obs["seconds_left"]) if obs.get("seconds_left") is not None else None,
            btc_price=btc,
            btc_delta_vs_strike=delta,
            yes_bid=obs.get("yes_bid"),
            yes_ask=yes_ask,
            no_bid=obs.get("no_bid"),
            no_ask=no_ask,
            btc_move_to_close=close_btc - btc,
            yes_ask_move_to_close=(
                (float(close_yes_ask) - float(yes_ask))
                if close_yes_ask is not None and yes_ask is not None else None
            ),
            no_ask_move_to_close=(
                (float(close_no_ask) - float(no_ask))
                if close_no_ask is not None and no_ask is not None else None
            ),
        ))
    return out


def _max_bid_after(observations: list[dict], start_idx: int, side: str) -> float | None:
    best: float | None = None
    for obs in observations[start_idx:]:
        bid = obs.get("yes_bid") if side == "YES" else obs.get("no_bid")
        if bid is None:
            continue
        val = float(bid)
        if best is None or val > best:
            best = val
    return best


def collect_tp_samples(observations: list[dict]) -> list[TpSample]:
    """For each valid entry point, test whether TP bid levels are reached before close."""
    samples: list[TpSample] = []
    for idx, obs in enumerate(observations):
        sl = obs.get("seconds_left")
        if sl is None or int(sl) < MIN_SECONDS_LEFT_FOR_TP:
            continue
        for side in ("YES", "NO"):
            ask_key = "yes_ask" if side == "YES" else "no_ask"
            entry_ask = obs.get(ask_key)
            if entry_ask is None or float(entry_ask) <= 0.05 or float(entry_ask) >= 0.95:
                continue
            entry_price = float(entry_ask)
            bucket, mid = entry_price_bucket(entry_price)
            max_bid = _max_bid_after(observations, idx, side)
            if max_bid is None:
                continue
            for tp in TP_LEVELS:
                if tp <= entry_price:
                    continue
                samples.append(TpSample(
                    side=side,
                    entry_bucket=bucket,
                    entry_price_mid=mid,
                    tp_level=tp,
                    reached=max_bid >= tp,
                ))
    return samples


def aggregate_tp_samples(samples: list[TpSample]) -> list[TpAggregate]:
    counts: dict[tuple[str, str, float, float], list[bool]] = {}
    for s in samples:
        key = (s.side, s.entry_bucket, s.entry_price_mid, s.tp_level)
        counts.setdefault(key, []).append(s.reached)
    out: list[TpAggregate] = []
    for (side, bucket, mid, tp), hits in sorted(counts.items()):
        out.append(TpAggregate(
            side=side,
            entry_bucket=bucket,
            entry_price_mid=mid,
            tp_level=tp,
            sample_count=len(hits),
            reach_count=sum(1 for h in hits if h),
        ))
    return out
