"""Polymarket HTF market discovery — research/collector only."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from bot.config import GAMMA_API
from bot.research.mtf.config import (
    SLUG_15M_PREFIX,
    TF_15M_SECONDS,
    TF_1H_SECONDS,
    TF_5M_SECONDS,
)

logger = logging.getLogger(__name__)


@dataclass
class HtfMarketRef:
    timeframe: str
    slug: str
    title: str
    window_start_ts: int | None
    end_ts: int | None
    active: bool


def _align_window(ts: int, window_sec: int) -> int:
    return (ts // window_sec) * window_sec


def slug_15m_at(ts: int) -> str:
    return f"{SLUG_15M_PREFIX}-{_align_window(ts, TF_15M_SECONDS)}"


def slug_5m_at(ts: int) -> str:
    return f"btc-updown-5m-{_align_window(ts, TF_5M_SECONDS)}"


def _fetch_gamma_event(slug: str) -> dict[str, Any] | None:
    try:
        resp = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=5)
        resp.raise_for_status()
        events = resp.json()
        return events[0] if events else None
    except Exception as exc:
        logger.debug("Gamma fetch failed for %s: %s", slug, exc)
        return None


def discover_15m_market(ts: int | None = None) -> HtfMarketRef | None:
    ts = ts or int(time.time())
    for offset in (0, -TF_15M_SECONDS):
        slug = slug_15m_at(ts + offset)
        event = _fetch_gamma_event(slug)
        if not event:
            continue
        ws = _align_window(ts + offset, TF_15M_SECONDS)
        return HtfMarketRef(
            timeframe="15m",
            slug=slug,
            title=event.get("title") or "",
            window_start_ts=ws,
            end_ts=ws + TF_15M_SECONDS,
            active=bool(event.get("active")) and not event.get("closed"),
        )
    return None


def search_gamma_btc_markets(limit: int = 50) -> list[dict[str, Any]]:
    """Search Gamma for active BTC up/down markets across timeframes."""
    results: list[dict[str, Any]] = []
    queries = [
        ("btc-updown-15m", "15m"),
        ("btc-updown-5m", "5m"),
        ("bitcoin-up-or-down", "1h"),
    ]
    seen: set[str] = set()
    for q, default_tf in queries:
        try:
            resp = requests.get(
                f"{GAMMA_API}/events",
                params={"limit": limit, "active": "true", "closed": "false", "q": q},
                timeout=8,
            )
            resp.raise_for_status()
            for ev in resp.json() or []:
                slug = (ev.get("slug") or "").lower()
                title = (ev.get("title") or "").lower()
                if slug in seen:
                    continue
                if "btc" not in slug and "bitcoin" not in slug and "btc" not in title:
                    continue
                if "up" not in slug and "up" not in title:
                    continue
                if "down" not in slug and "down" not in title:
                    continue
                seen.add(slug)
                if "15m" in slug or "15m" in title:
                    tf = "15m"
                elif "on-" in slug or "daily" in title:
                    tf = "daily"
                elif "5m" in slug:
                    tf = "5m"
                else:
                    tf = default_tf
                results.append({
                    "timeframe": tf,
                    "slug": ev.get("slug"),
                    "title": ev.get("title"),
                    "active": ev.get("active"),
                    "closed": ev.get("closed"),
                })
        except Exception as exc:
            logger.debug("Gamma search failed for %s: %s", q, exc)
    return results


def discover_active_htf_markets(ts: int | None = None) -> dict[str, HtfMarketRef | None]:
    """Best-effort discovery of active 15m/1h/daily BTC markets at timestamp."""
    ts = ts or int(time.time())
    out: dict[str, HtfMarketRef | None] = {"15m": discover_15m_market(ts), "1h": None, "daily": None}

    # 1h/daily: search Gamma (slug not deterministic UTC)
    try:
        for item in search_gamma_btc_markets(limit=30):
            slug = item.get("slug") or ""
            tf = item.get("timeframe")
            if tf == "1h" and out["1h"] is None and "bitcoin-up-or-down" in slug:
                if "on-" not in slug:  # exclude daily pattern
                    out["1h"] = HtfMarketRef(
                        timeframe="1h",
                        slug=slug,
                        title=item.get("title") or "",
                        window_start_ts=None,
                        end_ts=None,
                        active=True,
                    )
            if tf == "daily" and out["daily"] is None and "on-" in slug:
                out["daily"] = HtfMarketRef(
                    timeframe="daily",
                    slug=slug,
                    title=item.get("title") or "",
                    window_start_ts=None,
                    end_ts=None,
                    active=True,
                )
    except Exception:
        pass

    return out
