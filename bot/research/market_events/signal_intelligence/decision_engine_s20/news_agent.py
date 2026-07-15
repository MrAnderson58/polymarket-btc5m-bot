"""S2.0 News Agent — RSS / CryptoPanic / X interface (no Claude)."""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bot.research.market_events.signal_intelligence.decision_engine_s20.x_twitter import (
    XClientInterface,
)

logger = logging.getLogger(__name__)

RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("theblock", "https://www.theblock.co/rss.xml"),
    ("decrypt", "https://decrypt.co/feed"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
)

_BULLISH = (
    "surge", "rally", "soar", "jump", "gain", "bull", "etf inflow", "approval",
    "breakout", "all-time high", "ath", "record high", "accumulate", "inflow",
    "partnership", "upgrade", "adoption", "buy",
)
_BEARISH = (
    "crash", "plunge", "dump", "bear", "hack", "exploit", "lawsuit", "ban",
    "sec charge", "outflow", "liquidation cascade", "sell-off", "selloff",
    "fraud", "collapse", "downturn", "rejection",
)
_HIGH_IMP = (
    "etf", "fed", "sec", "hack", "exploit", "ban", "approval", "rate decision",
    "cpi", "fomc", "blackrock", "binance", "liquidation",
)


def _http_get(url: str, *, timeout: float = 8.0) -> str:
    req = Request(url, headers={"User-Agent": "polymarket-bot-s20/1.0"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — research RSS only
        return resp.read().decode("utf-8", errors="replace")


def _parse_rss_items(xml_text: str, *, limit: int = 12) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    # RSS 2.0 + Atom
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag != "item" and tag != "entry":
            continue
        title = ""
        summary = ""
        for child in node:
            ctag = child.tag.split("}")[-1].lower()
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag in ("description", "summary", "content") and child.text and not summary:
                summary = re.sub(r"<[^>]+>", " ", child.text).strip()
        if title:
            items.append({"title": title, "summary": summary[:280]})
        if len(items) >= limit:
            break
    return items


def fetch_rss_headlines_s20(*, limit_per_feed: int = 8) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name, url in RSS_FEEDS:
        try:
            body = _http_get(url)
            for it in _parse_rss_items(body, limit=limit_per_feed):
                it["source"] = name
                out.append(it)
        except Exception as exc:
            logger.debug("RSS %s failed: %s", name, exc)
    return out


def fetch_cryptopanic_s20(*, symbol: str, limit: int = 15) -> list[dict[str, str]]:
    token = os.getenv("CRYPTOPANIC_AUTH_TOKEN", "").strip() or os.getenv("CRYPTOPANIC_TOKEN", "").strip()
    params: dict[str, str] = {
        "currencies": symbol.upper().replace("USDT", ""),
        "public": "true",
        "kind": "news",
    }
    if token:
        params["auth_token"] = token
    url = "https://cryptopanic.com/api/v1/posts/?" + urlencode(params)
    try:
        import json
        raw = _http_get(url)
        data = json.loads(raw)
        results = []
        for row in (data.get("results") or [])[:limit]:
            title = (row.get("title") or "").strip()
            if title:
                results.append({
                    "title": title,
                    "summary": "",
                    "source": "cryptopanic",
                    "sentiment_hint": str((row.get("votes") or {}).get("positive") or ""),
                })
        return results
    except Exception as exc:
        logger.debug("CryptoPanic failed: %s", exc)
        return []


def _score_text(text: str) -> tuple[float, list[str]]:
    t = text.lower()
    score = 0.0
    hits: list[str] = []
    for w in _BULLISH:
        if w in t:
            score += 1.0
            hits.append(f"+{w}")
    for w in _BEARISH:
        if w in t:
            score -= 1.0
            hits.append(f"-{w}")
    return score, hits


def _importance(texts: list[str]) -> str:
    blob = " ".join(texts).lower()
    hits = sum(1 for w in _HIGH_IMP if w in blob)
    if hits >= 3:
        return "high"
    if hits >= 1:
        return "medium"
    return "low"


def score_headlines_s20(
    headlines: list[dict[str, str]],
    *,
    symbol: str,
) -> dict[str, Any]:
    sym = symbol.upper().replace("USDT", "")
    relevant = []
    total = 0.0
    reason_bits: list[str] = []
    for h in headlines:
        title = h.get("title") or ""
        summary = h.get("summary") or ""
        blob = f"{title} {summary}"
        if sym.lower() not in blob.lower() and sym not in ("BTC", "ETH") and "bitcoin" not in blob.lower() and "crypto" not in blob.lower():
            # Keep general crypto flow for majors
            if sym in ("BTC", "ETH", "SOL"):
                pass
            else:
                continue
        sc, hits = _score_text(blob)
        if hits or sym.lower() in blob.lower() or (sym == "BTC" and "bitcoin" in blob.lower()):
            relevant.append(h)
            total += sc
            src = h.get("source") or "news"
            reason_bits.append(f"[{src}] {title[:90]}" + (f" ({', '.join(hits[:3])})" if hits else ""))

    n = max(1, len(relevant) or len(headlines[:5]))
    # if nothing relevant, score top headlines weakly
    if not relevant:
        for h in headlines[:6]:
            sc, hits = _score_text(h.get("title") or "")
            total += sc * 0.5
            if hits:
                reason_bits.append(f"[{h.get('source')}] {(h.get('title') or '')[:90]}")

    avg = total / n
    if avg > 0.35:
        sentiment = "bullish"
    elif avg < -0.35:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    conf = min(0.95, max(0.3, 0.4 + min(0.45, abs(avg) * 0.25) + min(0.2, len(reason_bits) * 0.03)))
    texts = [h.get("title") or "" for h in (relevant or headlines[:8])]
    importance = _importance(texts)

    reasons = reason_bits[:6] or ["No strong keyword hits — neutral news tape"]
    return {
        "sentiment": sentiment,
        "confidence": round(conf, 3),
        "importance": importance,
        "reasons": reasons,
        "meta": {
            "headline_count": len(headlines),
            "relevant_count": len(relevant),
            "score_avg": round(avg, 3),
        },
    }


def run_news_agent_s20(
    symbol: str,
    *,
    headlines: list[dict[str, str]] | None = None,
    x_client: XClientInterface | None = None,
) -> dict[str, Any]:
    """News sentiment JSON. X is optional interface-only."""
    sym = symbol.upper().replace("USDT", "").strip() or "BTC"
    collected = list(headlines) if headlines is not None else []
    if headlines is None:
        collected.extend(fetch_rss_headlines_s20())
        collected.extend(fetch_cryptopanic_s20(symbol=sym))

    x = x_client or XClientInterface()
    x_items = x.fetch_recent(symbol=sym, limit=10)
    for tw in x_items:
        collected.append({
            "title": tw.text[:200],
            "summary": "",
            "source": f"x:@{tw.account}",
        })

    result = score_headlines_s20(collected, symbol=sym)
    result["meta"] = {
        **(result.get("meta") or {}),
        "x_status": x.status(),
        "x_tweets": len(x_items),
    }
    return result
