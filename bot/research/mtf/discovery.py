"""Polymarket HTF market discovery — research/collector only."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import GAMMA_API
from bot.research.mtf.http_client import DEFAULT_HTTP_TIMEOUT, http_get_json
from bot.research.mtf.config import (
    SLUG_15M_PREFIX,
    TF_15M_SECONDS,
    TF_1H_SECONDS,
    TF_5M_SECONDS,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Diagnostic reason codes
NO_CANDIDATE = "NO_CANDIDATE"
CANDIDATE_CLOSED = "CANDIDATE_CLOSED"
TOKEN_IDS_MISSING = "TOKEN_IDS_MISSING"
QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
STRIKE_UNAVAILABLE = "STRIKE_UNAVAILABLE"
TIME_PARSE_FAILED = "TIME_PARSE_FAILED"


@dataclass
class HtfMarketRef:
    timeframe: str
    slug: str
    title: str
    window_start_ts: int | None
    end_ts: int | None
    active: bool


@dataclass
class TfDiagnostic:
    timeframe: str
    candidate_patterns: list[str] = field(default_factory=list)
    candidates_found: list[str] = field(default_factory=list)
    selected_slug: str | None = None
    active_interval: str | None = None
    quotes_available: bool = False
    strike: float | None = None
    strike_source: str | None = None
    seconds_left: int | None = None
    reason: str | None = None
    errors: list[str] = field(default_factory=list)


def _align_window(ts: int, window_sec: int) -> int:
    return (ts // window_sec) * window_sec


def slug_5m_at(ts: int) -> str:
    return f"btc-updown-5m-{_align_window(ts, TF_5M_SECONDS)}"


def slug_15m_at(ts: int) -> str:
    return f"{SLUG_15M_PREFIX}-{_align_window(ts, TF_15M_SECONDS)}"


def parse_15m_window_start_ts(slug: str) -> int | None:
    """Parse window_start_ts from ``btc-updown-15m-{window_start_ts}``."""
    m = re.match(rf"^{re.escape(SLUG_15M_PREFIX)}-(\d+)$", slug)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def compute_seconds_left(end_ts: int | None, snapshot_ts: int) -> int | None:
    if end_ts is None:
        return None
    return max(0, end_ts - snapshot_ts)


def seconds_left_for_ref(ref: HtfMarketRef, snapshot_ts: int) -> int | None:
    """Compute seconds_left for a discovered market at snapshot time."""
    if ref.timeframe == "15m":
        ws = ref.window_start_ts
        if ws is None:
            ws = parse_15m_window_start_ts(ref.slug)
        if ws is None:
            return None
        return compute_seconds_left(ws + TF_15M_SECONDS, snapshot_ts)
    return compute_seconds_left(ref.end_ts, snapshot_ts)


def _hour_label_et(dt: datetime) -> str:
    hour = dt.hour
    if hour == 0:
        return "12am"
    if hour < 12:
        return f"{hour}am"
    if hour == 12:
        return "12pm"
    return f"{hour - 12}pm"


def slug_1h_at(ts: int) -> str:
    """Build confirmed production hourly slug: ``bitcoin-up-or-down-july-6-2026-1pm-et``."""
    dt = datetime.fromtimestamp(ts, tz=ET)
    month = dt.strftime("%B").lower()
    return f"bitcoin-up-or-down-{month}-{dt.day}-{dt.year}-{_hour_label_et(dt)}-et"


def slug_daily_at(ts: int) -> str:
    """Build confirmed production daily slug: ``bitcoin-up-or-down-on-july-6-2026``."""
    dt = datetime.fromtimestamp(ts, tz=ET)
    month = dt.strftime("%B").lower()
    return f"bitcoin-up-or-down-on-{month}-{dt.day}-{dt.year}"


def _end_ts_from_gamma_event(event: dict[str, Any]) -> int | None:
    end_date = event.get("endDate")
    if not end_date:
        return None
    try:
        dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def _fetch_gamma_event(slug: str) -> dict[str, Any] | None:
    events, reason = http_get_json(
        f"{GAMMA_API}/events",
        params={"slug": slug},
        timeout=DEFAULT_HTTP_TIMEOUT,
    )
    if reason:
        logger.warning("Gamma fetch failed for %s: %s", slug, reason)
        return None
    return events[0] if events else None


def _ref_from_gamma_event(
    timeframe: str,
    slug: str,
    event: dict[str, Any],
    *,
    window_start_ts: int | None = None,
) -> HtfMarketRef:
    end_ts = _end_ts_from_gamma_event(event)
    return HtfMarketRef(
        timeframe=timeframe,
        slug=slug,
        title=event.get("title") or "",
        window_start_ts=window_start_ts,
        end_ts=end_ts,
        active=bool(event.get("active")) and not event.get("closed"),
    )


def _is_active_unexpired(event: dict[str, Any], ts: int) -> bool:
    if event.get("closed") or not event.get("active"):
        return False
    end_ts = _end_ts_from_gamma_event(event)
    if end_ts is not None and end_ts <= ts:
        return False
    return True


def discover_15m_market(ts: int | None = None) -> HtfMarketRef | None:
    ts = ts or int(time.time())
    for offset in (0, -TF_15M_SECONDS):
        aligned = _align_window(ts + offset, TF_15M_SECONDS)
        slug = f"{SLUG_15M_PREFIX}-{aligned}"
        event = _fetch_gamma_event(slug)
        if not event:
            continue
        if not _is_active_unexpired(event, ts):
            continue
        return _ref_from_gamma_event("15m", slug, event, window_start_ts=aligned)
    return None


def discover_1h_market(ts: int | None = None) -> HtfMarketRef | None:
    ts = ts or int(time.time())
    candidates: list[str] = []
    for offset_h in (0, -1, 1):
        candidate_ts = ts + offset_h * TF_1H_SECONDS
        slug = slug_1h_at(candidate_ts)
        candidates.append(slug)
        event = _fetch_gamma_event(slug)
        if not event:
            continue
        if not _is_active_unexpired(event, ts):
            continue
        return _ref_from_gamma_event("1h", slug, event)
    logger.debug("1h discovery failed; tried: %s", candidates)
    return None


def discover_daily_market(ts: int | None = None) -> HtfMarketRef | None:
    ts = ts or int(time.time())
    dt_et = datetime.fromtimestamp(ts, tz=ET)
    candidates: list[str] = []
    for day_offset in (0, 1, -1):
        day_ts = int((dt_et + timedelta(days=day_offset)).timestamp())
        slug = slug_daily_at(day_ts)
        candidates.append(slug)
        event = _fetch_gamma_event(slug)
        if not event:
            continue
        if not _is_active_unexpired(event, ts):
            continue
        return _ref_from_gamma_event("daily", slug, event)
    logger.debug("daily discovery failed; tried: %s", candidates)
    return None


def discover_active_htf_markets(ts: int | None = None) -> dict[str, HtfMarketRef | None]:
    """Best-effort discovery of active 15m/1h/daily BTC markets at timestamp."""
    ts = ts or int(time.time())
    return {
        "15m": discover_15m_market(ts),
        "1h": discover_1h_market(ts),
        "daily": discover_daily_market(ts),
    }


def _diagnose_5m(ts: int) -> TfDiagnostic:
    diag = TfDiagnostic(timeframe="5m")
    slug = slug_5m_at(ts)
    diag.candidate_patterns = [slug]
    event = _fetch_gamma_event(slug)
    if event:
        diag.candidates_found = [slug]
        diag.selected_slug = slug
    else:
        diag.reason = NO_CANDIDATE
        diag.errors.append(f"5m slug not found: {slug}")
    return diag


def _diagnose_15m(ts: int) -> TfDiagnostic:
    diag = TfDiagnostic(timeframe="15m")
    patterns = [
        slug_15m_at(ts),
        slug_15m_at(ts - TF_15M_SECONDS),
    ]
    diag.candidate_patterns = patterns
    ref = discover_15m_market(ts)
    if ref is None:
        for slug in patterns:
            event = _fetch_gamma_event(slug)
            if event:
                diag.candidates_found.append(slug)
                if event.get("closed") or not event.get("active"):
                    diag.reason = CANDIDATE_CLOSED
                else:
                    end_ts = _end_ts_from_gamma_event(event)
                    if end_ts is not None and end_ts <= ts:
                        diag.reason = CANDIDATE_CLOSED
                    else:
                        diag.reason = NO_CANDIDATE
                break
        if not diag.candidates_found:
            diag.reason = NO_CANDIDATE
            diag.errors.append("no 15m candidate found via slug lookup")
        return diag

    diag.candidates_found = [ref.slug]
    diag.selected_slug = ref.slug
    diag.seconds_left = seconds_left_for_ref(ref, ts)
    if diag.seconds_left is None:
        diag.reason = TIME_PARSE_FAILED
        diag.errors.append("could not compute 15m seconds_left")
    return diag


def _diagnose_htf(
    ts: int,
    timeframe: str,
    discover_fn: Any,
    pattern_fn: Any,
    *,
    extra_patterns: list[str] | None = None,
) -> TfDiagnostic:
    diag = TfDiagnostic(timeframe=timeframe)
    patterns = list(extra_patterns or [])
    patterns.append(pattern_fn(ts))
    if timeframe == "1h":
        patterns.extend([
            pattern_fn(ts - TF_1H_SECONDS),
            pattern_fn(ts + TF_1H_SECONDS),
        ])
    elif timeframe == "daily":
        dt_et = datetime.fromtimestamp(ts, tz=ET)
        patterns.append(pattern_fn(int((dt_et + timedelta(days=1)).timestamp())))
        patterns.append(pattern_fn(int((dt_et - timedelta(days=1)).timestamp())))
    diag.candidate_patterns = patterns

    for slug in patterns:
        event = _fetch_gamma_event(slug)
        if event:
            diag.candidates_found.append(slug)

    ref = discover_fn(ts)
    if ref is None:
        if not diag.candidates_found:
            diag.reason = NO_CANDIDATE
            diag.errors.append(f"no {timeframe} candidate found")
        else:
            diag.reason = CANDIDATE_CLOSED
            diag.errors.append(f"{timeframe} candidates exist but none active/unexpired")
        return diag

    diag.selected_slug = ref.slug
    diag.seconds_left = seconds_left_for_ref(ref, ts)
    return diag


def _enrich_diagnostic_quotes(diag: TfDiagnostic, ts: int) -> None:
    if not diag.selected_slug:
        return
    try:
        from bot.research.mtf.alignment import active_interval_label
        from bot.market_scanner import get_token_ids_for_market_slug
        from bot.research.mtf.quotes import fetch_mtf_market_quotes
        from bot.research.mtf.strike_resolver import resolve_strike

        diag.active_interval = active_interval_label(diag.selected_slug, diag.timeframe)

        token_ids = get_token_ids_for_market_slug(diag.selected_slug)
        if not token_ids:
            if diag.reason is None:
                diag.reason = TOKEN_IDS_MISSING
            diag.errors.append("token IDs missing for selected slug")
            return

        quotes = fetch_mtf_market_quotes(diag.selected_slug)
        if not quotes or quotes.get("yes_ask") is None:
            if diag.reason is None:
                diag.reason = QUOTE_UNAVAILABLE
            diag.errors.append("quotes unavailable")
        else:
            diag.quotes_available = True

        strike_result = resolve_strike(diag.selected_slug, diag.timeframe, snapshot_ts=ts)
        diag.strike = strike_result.strike
        diag.strike_source = strike_result.strike_source
        if strike_result.strike is None and diag.reason is None:
            diag.reason = STRIKE_UNAVAILABLE
        if strike_result.resolution_notes:
            diag.errors.append(strike_result.resolution_notes)

        if diag.seconds_left is None and diag.timeframe == "15m":
            ws = parse_15m_window_start_ts(diag.selected_slug)
            if ws is None:
                if diag.reason is None:
                    diag.reason = TIME_PARSE_FAILED
            else:
                diag.seconds_left = compute_seconds_left(ws + TF_15M_SECONDS, ts)
    except Exception as exc:
        diag.errors.append(str(exc))
        if diag.reason is None:
            diag.reason = QUOTE_UNAVAILABLE


def diagnose_discovery(ts: int | None = None) -> dict[str, TfDiagnostic]:
    ts = ts or int(time.time())
    out = {
        "5m": _diagnose_5m(ts),
        "15m": _diagnose_15m(ts),
        "1h": _diagnose_htf(ts, "1h", discover_1h_market, slug_1h_at),
        "daily": _diagnose_htf(ts, "daily", discover_daily_market, slug_daily_at),
    }
    for tf in ("15m", "1h", "daily"):
        _enrich_diagnostic_quotes(out[tf], ts)
    return out


def render_diagnose_discovery(diagnostics: dict[str, TfDiagnostic]) -> str:
    lines: list[str] = []
    for tf in ("5m", "15m", "1h", "daily"):
        d = diagnostics[tf]
        lines.append(f"{tf}:")
        if tf == "5m":
            lines.append(f"  slug: {d.selected_slug or 'NOT FOUND'}")
            lines.append(f"  found: {'yes' if d.selected_slug else 'no'}")
            if d.reason:
                lines.append(f"  reason: {d.reason}")
        elif tf == "15m":
            lines.append(f"  candidate patterns: {', '.join(d.candidate_patterns)}")
            lines.append(f"  selected slug: {d.selected_slug or 'NONE'}")
            lines.append(f"  active interval: {d.active_interval or 'NONE'}")
            lines.append(f"  quotes available: {'yes' if d.quotes_available else 'no'}")
            lines.append(f"  strike: {d.strike if d.strike is not None else 'NONE'}")
            lines.append(f"  strike source: {d.strike_source or 'NONE'}")
            lines.append(f"  seconds_left: {d.seconds_left if d.seconds_left is not None else 'NULL'}")
            if d.reason:
                lines.append(f"  reason: {d.reason}")
        else:
            lines.append(f"  candidates found: {len(d.candidates_found)}")
            if d.candidates_found:
                lines.append(f"  candidate slugs: {', '.join(d.candidates_found[:5])}")
            lines.append(f"  selected slug: {d.selected_slug or 'NONE'}")
            lines.append(f"  active interval: {d.active_interval or 'NONE'}")
            lines.append(f"  quotes available: {'yes' if d.quotes_available else 'no'}")
            lines.append(f"  strike: {d.strike if d.strike is not None else 'NONE'}")
            lines.append(f"  strike source: {d.strike_source or 'NONE'}")
            lines.append(f"  seconds_left: {d.seconds_left if d.seconds_left is not None else 'NULL'}")
            if d.reason:
                lines.append(f"  reason: {d.reason}")
        for err in d.errors:
            lines.append(f"  error: {err}")
        lines.append("")
    return "\n".join(lines).rstrip()
