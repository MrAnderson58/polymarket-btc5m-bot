"""Phase N1.1 — News Collector MVP. RSS-only ingest into market_news_feed_n11. No Claude."""

from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEDUP_RATIO = 0.90

# Display name → RSS URL (RSS only, no APIs).
RSS_FEEDS_N11: tuple[tuple[str, str], ...] = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
)

_SYMBOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("BTC", re.compile(r"\b(bitcoin|btc)\b", re.I)),
    ("ETH", re.compile(r"\b(ethereum|ether|eth)\b", re.I)),
    ("SOL", re.compile(r"\b(solana|sol)\b", re.I)),
    ("XRP", re.compile(r"\b(ripple|xrp)\b", re.I)),
    ("BNB", re.compile(r"\b(binance coin|bnb)\b", re.I)),
    ("DOGE", re.compile(r"\b(dogecoin|doge)\b", re.I)),
    ("ADA", re.compile(r"\b(cardano|ada)\b", re.I)),
    ("AVAX", re.compile(r"\b(avalanche|avax)\b", re.I)),
)

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("etf", ("etf", "exchange-traded")),
    ("hack", ("hack", "exploit", "breach", "stolen")),
    ("regulation", ("sec", "cftc", "lawsuit", "ban", "regulation", "court")),
    ("macro", ("fed", "cpi", "fomc", "interest rate", "inflation")),
    ("defi", ("defi", "protocol", "liquidity pool")),
)


def _http_get(url: str, *, timeout: float = 12.0) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — research RSS only
        return resp.read().decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _parse_published(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError, OverflowError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw.replace("Z", "+0000") if fmt.endswith("%z") else raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node:
        tag = child.tag.split("}")[-1].lower()
        if tag in names:
            if child.text and child.text.strip():
                return child.text.strip()
            # Atom: <link href="..."/>
            if tag == "link":
                href = child.attrib.get("href") or ""
                if href:
                    return href.strip()
    return ""


def _parse_rss_items(xml_text: str, *, source: str, limit: int = 25) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = _child_text(node, ("title",))
        if not title:
            continue
        summary = _strip_html(_child_text(node, ("description", "summary", "content")))
        url = _child_text(node, ("link", "id", "guid"))
        published_raw = _child_text(node, ("pubdate", "published", "updated", "date"))
        published_at = _parse_published(published_raw) or int(time.time())
        items.append({
            "source": source,
            "title": title.strip(),
            "summary": summary[:2000],
            "url": url[:500] if url else "",
            "published_at": published_at,
            "raw": {
                "source": source,
                "title": title.strip(),
                "summary": summary[:500],
                "url": url,
                "published_raw": published_raw,
            },
        })
        if len(items) >= limit:
            break
    return items


def extract_symbols_n11(title: str, summary: str = "") -> list[str]:
    text = f"{title} {summary}"
    try:
        from bot.research.market_events.signal_intelligence.news_intelligence.watchlist import (
            detect_symbols,
        )
        found = detect_symbols(text)
        if found:
            return found
    except Exception:
        pass
    found: list[str] = []
    for sym, pat in _SYMBOL_PATTERNS:
        if pat.search(text) and sym not in found:
            found.append(sym)
    return found


def extract_category_n11(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    for cat, keys in _CATEGORY_KEYWORDS:
        if any(k in text for k in keys):
            return cat
    return "general"


def title_similarity_n11(a: str, b: str) -> float:
    na = re.sub(r"\s+", " ", (a or "").strip().lower())
    nb = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def is_duplicate_title_n11(title: str, existing_titles: list[str], *, threshold: float = DEDUP_RATIO) -> bool:
    for prev in existing_titles:
        if title_similarity_n11(title, prev) >= threshold:
            return True
    return False


def fetch_all_rss_n11(*, limit_per_feed: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source, url in RSS_FEEDS_N11:
        try:
            body = _http_get(url)
            items = _parse_rss_items(body, source=source, limit=limit_per_feed)
            out.extend(items)
            logger.info("n11 RSS %s: %d items", source, len(items))
        except Exception as exc:
            logger.warning("n11 RSS %s failed: %s", source, exc)
    return out


def _ensure_table(conn: Any) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_news_feed_n11'"
    ).fetchone()
    if row is None:
        from bot.research.market_events.event_schema import apply_migrations
        apply_migrations(conn)


def _load_recent_titles(conn: Any, *, limit: int = 800) -> list[str]:
    rows = conn.execute(
        "SELECT title FROM market_news_feed_n11 ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [str(r["title"] if hasattr(r, "keys") else r[0]) for r in rows]


def insert_news_item_n11(conn: Any, item: dict[str, Any], *, now: int | None = None) -> int | None:
    """INSERT one row into market_news_feed_n11 only. Returns id or None if skipped."""
    created = now if now is not None else int(time.time())
    title = str(item.get("title") or "")
    body = str(item.get("body") or item.get("summary") or "")
    symbols = item.get("symbols")
    if symbols is None:
        symbols = extract_symbols_n11(title, body)
    if isinstance(symbols, list):
        symbols_str = ",".join(str(s) for s in symbols)[:200]
    else:
        symbols_str = str(symbols or "")[:200]
    category = item.get("category") or extract_category_n11(title, body)
    raw_json = item.get("raw_json")
    if raw_json is None:
        raw_json = json.dumps(item.get("raw") or item, ensure_ascii=False, default=str)

    try:
        from bot.research.market_events.signal_intelligence.news_intelligence.tagging import (
            tag_news_item,
        )
        tagged = tag_news_item(
            timestamp=int(item.get("published_at") or created),
            source=str(item.get("source") or "unknown"),
            title=title,
            body=body,
            url=str(item.get("url") or ""),
            symbols=symbols if isinstance(symbols, list) else None,
        )
        source_type = str(item.get("source_type") or tagged["source_type"] or "rss")
        if item.get("importance") is not None:
            importance = float(item.get("importance"))
        else:
            importance = float(tagged["importance"])
        language = str(item.get("language") or tagged["language"] or "en")
        body_store = str(item.get("body") or tagged["body"] or body)[:4000]
        symbols_str = (
            ",".join(tagged["symbols"])[:200] if tagged["symbols"] else symbols_str
        )
    except Exception:
        source_type = str(item.get("source_type") or "rss")
        importance = float(item.get("importance") or 0.35)
        language = str(item.get("language") or "en")
        body_store = body[:4000]

    # Prefer enriched columns when present; fall back to base N11 schema.
    try:
        cur = conn.execute(
            """
            INSERT INTO market_news_feed_n11 (
                published_at, source, title, summary, url, symbols, category,
                raw_json, created_at, source_type, importance, language, body
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(item.get("published_at") or created),
                str(item.get("source") or "unknown")[:80],
                title[:500],
                str(item.get("summary") or body)[:2000],
                str(item.get("url") or "")[:500],
                symbols_str,
                str(category or "general")[:64],
                raw_json,
                created,
                source_type[:32],
                importance,
                language[:8],
                body_store[:4000],
            ),
        )
    except Exception:
        cur = conn.execute(
            """
            INSERT INTO market_news_feed_n11 (
                published_at, source, title, summary, url, symbols, category,
                raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(item.get("published_at") or created),
                str(item.get("source") or "unknown")[:80],
                title[:500],
                str(item.get("summary") or body)[:2000],
                str(item.get("url") or "")[:500],
                symbols_str,
                str(category or "general")[:64],
                raw_json,
                created,
            ),
        )
    return int(cur.lastrowid) if cur.lastrowid else None


def run_news_update_n11(
    conn: Any,
    *,
    limit_per_feed: int = 20,
    commit: bool = True,
) -> dict[str, Any]:
    """Fetch RSS feeds from config (fallback to built-in list) and INSERT."""
    _ensure_table(conn)
    try:
        from bot.research.market_events.signal_intelligence.multi_source.rss_collector import (
            load_rss_feeds_from_config,
        )
        feeds = load_rss_feeds_from_config() or list(RSS_FEEDS_N11)
    except Exception:
        feeds = list(RSS_FEEDS_N11)

    fetched_items: list[dict[str, Any]] = []
    for source, url in feeds:
        try:
            body = _http_get(url)
            items = _parse_rss_items(body, source=source, limit=limit_per_feed)
            fetched_items.extend(items)
            logger.info("n11 RSS %s: %d items", source, len(items))
        except Exception as exc:
            logger.warning("n11 RSS %s failed: %s", source, exc)

    existing = _load_recent_titles(conn)
    fetched_items.sort(key=lambda x: int(x.get("published_at") or 0), reverse=True)

    inserted = 0
    skipped_dup = 0
    batch_titles: list[str] = list(existing)
    now = int(time.time())
    for item in fetched_items:
        title = item.get("title") or ""
        if is_duplicate_title_n11(title, batch_titles):
            skipped_dup += 1
            continue
        item["symbols"] = extract_symbols_n11(title, item.get("summary") or "")
        item["category"] = extract_category_n11(title, item.get("summary") or "")
        item["source_type"] = "rss"
        insert_news_item_n11(conn, item, now=now)
        batch_titles.insert(0, title)
        inserted += 1

    if commit:
        conn.commit()

    total = conn.execute("SELECT COUNT(*) AS n FROM market_news_feed_n11").fetchone()["n"]
    return {
        "fetched": len(fetched_items),
        "inserted": inserted,
        "skipped_duplicates": skipped_dup,
        "total": int(total),
        "sources": [name for name, _ in feeds],
    }


def fetch_latest_news_n11(conn: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    _ensure_table(conn)
    rows = conn.execute(
        """
        SELECT id, published_at, source, title, summary, url, symbols, category, created_at
        FROM market_news_feed_n11
        ORDER BY published_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r["id"],
            "published_at": r["published_at"],
            "source": r["source"],
            "title": r["title"],
            "summary": r["summary"],
            "url": r["url"],
            "symbols": r["symbols"],
            "category": r["category"],
            "created_at": r["created_at"],
        })
    return out


def format_news_latest_n11(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "News Feed\n\n(no items — run news-update)\n"
    lines = ["News Feed", ""]
    for row in rows:
        lines.append(str(row.get("source") or "—"))
        title = str(row.get("title") or "—")
        sym = row.get("symbols") or ""
        if sym:
            lines.append(f"{title}  [{sym}]")
        else:
            lines.append(title)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_news_update_report_n11(result: dict[str, Any]) -> str:
    return "\n".join([
        "News Update (N1.1)",
        "",
        f"Fetched: {result.get('fetched', 0)}",
        f"Inserted: {result.get('inserted', 0)}",
        f"Skipped duplicates: {result.get('skipped_duplicates', 0)}",
        f"Total in feed: {result.get('total', 0)}",
        f"Sources: {', '.join(result.get('sources') or [])}",
        "",
        "Table: market_news_feed_n11 only",
        "No Claude",
    ]) + "\n"


def news_feed_dashboard_n11(conn: Any, *, limit: int = 50) -> dict[str, Any]:
    rows = fetch_latest_news_n11(conn, limit=limit)
    return {
        "tab": "News Feed",
        "count": len(rows),
        "items": [
            {
                "id": r["id"],
                "source": r["source"],
                "title": r["title"],
                "summary": (r.get("summary") or "")[:240],
                "url": r.get("url"),
                "symbols": r.get("symbols"),
                "category": r.get("category"),
                "published_at": r.get("published_at"),
            }
            for r in rows
        ],
    }
