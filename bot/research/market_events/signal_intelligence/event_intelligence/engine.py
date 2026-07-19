"""S43 — cluster N11 articles into market_intel_events."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any

from bot.research.market_events.db import execute_with_retry, market_events_connection
from bot.research.market_events.signal_intelligence.event_intelligence.confidence import (
    agreement_score,
    event_confidence,
    freshness_score,
)
from bot.research.market_events.signal_intelligence.event_intelligence.similarity import (
    article_similarity,
    cluster_key_bucket,
    event_uid_from_seed,
    significant_tokens,
)
from bot.research.market_events.signal_intelligence.narrative_engine.narratives import (
    detect_narratives,
    narratives_to_text,
)
from bot.research.market_events.signal_intelligence.narrative_engine.scoring import (
    importance_score,
    sentiment_scores,
)
from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    detect_symbols,
)

logger = logging.getLogger(__name__)

LOOKBACK_SEC = 24 * 3600
MERGE_THRESHOLD = 0.38
TIME_WINDOW_SEC = 24 * 3600
SECOND_PASS_THRESHOLD = 0.36



def _utc_now() -> int:
    return int(time.time())


def prepare_article(row: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "")
    body = str(row.get("body") or row.get("summary") or "")
    blob = f"{title}\n{body}"
    symbols = row.get("symbols")
    if not symbols:
        symbols = detect_symbols(blob)
    elif isinstance(symbols, str):
        symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbols = [str(s).upper() for s in symbols if s]
    narratives = detect_narratives(blob)
    tokens = significant_tokens(blob)
    entities = set(tokens) | {s.lower() for s in symbols}
    # Light entity boost for known narrative keywords already in tokens
    bull, bear, neu = sentiment_scores(title, body)
    sentiment = float(bull) - float(bear)
    return {
        "id": row.get("id"),
        "title": title,
        "body": body,
        "source": str(row.get("source") or "Unknown"),
        "url": str(row.get("url") or ""),
        "timestamp": int(row.get("timestamp") or row.get("published_at") or row.get("created_at") or 0),
        "symbols": symbols,
        "narratives": narratives,
        "tokens": tokens,
        "entities": entities,
        "bullish": bull,
        "bearish": bear,
        "neutral": neu,
        "sentiment": sentiment,
        "importance": importance_score(title, body),
    }


def cluster_articles(
    articles: list[dict[str, Any]],
    *,
    merge_threshold: float = MERGE_THRESHOLD,
    time_window_sec: int = TIME_WINDOW_SEC,
) -> list[list[dict[str, Any]]]:
    """Greedy clustering + second-pass thematic merge (target ~15–30 events / 90 arts)."""
    prepared = [prepare_article(a) if "tokens" not in a else a for a in articles]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for art in prepared:
        buckets[cluster_key_bucket(art)].append(art)

    clusters: list[list[dict[str, Any]]] = []
    duplicates_removed = 0

    for _bucket, group in buckets.items():
        group = sorted(group, key=lambda x: int(x.get("timestamp") or 0), reverse=True)
        local: list[list[dict[str, Any]]] = []
        for art in group:
            placed = False
            for cluster in local:
                best = 0.0
                for member in cluster[:12]:
                    sim = article_similarity(
                        art, member, time_window_sec=time_window_sec,
                    )
                    if sim > best:
                        best = sim
                if best >= merge_threshold:
                    same_title = (
                        (art.get("title") or "").strip().lower()
                        == (cluster[0].get("title") or "").strip().lower()
                    )
                    same_source = (
                        (art.get("source") or "").lower()
                        == (cluster[0].get("source") or "").lower()
                    )
                    if same_title and same_source:
                        duplicates_removed += 1
                        placed = True
                        break
                    cluster.append(art)
                    placed = True
                    break
            if not placed:
                local.append([art])
        clusters.extend(local)

    clusters, extra_dupes = _second_pass_merge(
        clusters, threshold=SECOND_PASS_THRESHOLD, time_window_sec=time_window_sec,
    )
    duplicates_removed += extra_dupes
    clusters, theme_merged = _thematic_bucket_merge(clusters)
    duplicates_removed += theme_merged
    cluster_articles.last_duplicates_removed = duplicates_removed  # type: ignore[attr-defined]
    return clusters


def _cluster_centroid(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    return max(cluster, key=lambda x: float(x.get("importance") or 0))


def _primary_theme_key(art: dict[str, Any]) -> str:
    narrs = [str(n) for n in (art.get("narratives") or []) if n and n != "General"]
    primary = narrs[0] if narrs else "General"
    syms = [str(s).upper() for s in (art.get("symbols") or []) if s]
    sym = syms[0] if syms else "MACRO"
    ts = int(art.get("timestamp") or 0)
    bucket = ts // (24 * 3600) if ts else 0
    theme_hits = {
        "etf", "fed", "fomc", "hack", "hyperliquid", "inflow", "inflows",
        "cpi", "ppi", "whale", "sec", "layer2", "defi",
    }
    toks = art.get("tokens") or set()
    tip = next((t for t in sorted(theme_hits) if t in toks), "")
    if tip and primary == "General":
        primary = tip.upper()
    return f"{primary}|{sym}|{tip}|{bucket}"


def _thematic_bucket_merge(
    clusters: list[list[dict[str, Any]]],
) -> tuple[list[list[dict[str, Any]]], int]:
    """Force-merge clusters that share narrative+symbol (or MACRO theme) same day."""
    if len(clusters) <= 1:
        return clusters, 0
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cluster in clusters:
        if not cluster:
            continue
        key = _primary_theme_key(_cluster_centroid(cluster))
        buckets[key].extend(cluster)
    merged = list(buckets.values())
    return merged, max(0, len(clusters) - len(merged))


def _second_pass_merge(
    clusters: list[list[dict[str, Any]]],
    *,
    threshold: float,
    time_window_sec: int,
) -> tuple[list[list[dict[str, Any]]], int]:
    """Merge clusters that share narrative/theme across coarse buckets."""
    if len(clusters) <= 1:
        return clusters, 0
    alive = [list(c) for c in clusters if c]
    merged_away = 0
    i = 0
    while i < len(alive):
        if not alive[i]:
            i += 1
            continue
        j = i + 1
        while j < len(alive):
            if not alive[j]:
                j += 1
                continue
            a = _cluster_centroid(alive[i])
            b = _cluster_centroid(alive[j])
            narr_a = set(a.get("narratives") or [])
            narr_b = set(b.get("narratives") or [])
            shared_narr = bool((narr_a & narr_b) - {"General"})
            theme_hits = {
                "etf", "fed", "fomc", "hack", "hyperliquid", "inflow", "inflows",
                "cpi", "ppi",
            }
            shared_theme = bool(
                theme_hits & (a.get("tokens") or set()) & (b.get("tokens") or set())
            )
            syms_a = {str(s).upper() for s in (a.get("symbols") or []) if s}
            syms_b = {str(s).upper() for s in (b.get("symbols") or []) if s}
            shared_sym = bool(syms_a & syms_b) or (not syms_a and not syms_b)
            if not (shared_narr or shared_theme):
                j += 1
                continue
            sim = article_similarity(a, b, time_window_sec=time_window_sec)
            if (
                sim >= threshold
                or (shared_narr and shared_theme)
                or (shared_narr and shared_sym and sim >= threshold * 0.7)
            ):
                alive[i].extend(alive[j])
                alive[j] = []
                merged_away += 1
            j += 1
        i += 1
    return [c for c in alive if c], merged_away


def build_event_from_cluster(
    cluster: list[dict[str, Any]],
    *,
    now: int,
) -> dict[str, Any]:
    cluster = sorted(cluster, key=lambda x: float(x.get("importance") or 0), reverse=True)
    titles = [str(a.get("title") or "") for a in cluster if a.get("title")]
    sources = sorted({str(a.get("source") or "Unknown") for a in cluster})
    symbols: list[str] = []
    for a in cluster:
        for s in a.get("symbols") or []:
            su = str(s).upper()
            if su and su not in symbols:
                symbols.append(su)
    narratives: list[str] = []
    for a in cluster:
        for n in a.get("narratives") or []:
            if n not in narratives:
                narratives.append(n)
    sentiments = [float(a.get("sentiment") or 0) for a in cluster]
    avg_sent = sum(sentiments) / len(sentiments) if sentiments else 0.0
    importance = max(float(a.get("importance") or 0) for a in cluster)
    first_seen = min(int(a.get("timestamp") or now) for a in cluster)
    last_seen = max(int(a.get("timestamp") or now) for a in cluster)
    fresh = freshness_score(last_seen, now=now)
    agree = agreement_score(sentiments)
    conf = event_confidence(
        sources=sources,
        headline_count=len(cluster),
        agreement=agree,
        freshness=fresh,
        importance=importance,
    )
    title = titles[0] if titles else "Untitled event"
    # Prefer a short thematic title when ETF/Fed etc.
    narr_text = narratives_to_text(narratives)
    if narratives:
        lead = narratives[0]
        if lead == "ETF":
            title = "ETF inflows accelerate" if avg_sent >= 0 else "ETF flows in focus"
        elif lead == "Fed":
            title = "Fed speakers remain hawkish" if avg_sent <= 0 else "Fed policy narrative"
        elif lead == "Hack":
            title = "Security incident / hack reports"
    summary_bits = [t for t in titles[:4]]
    summary = f"{title}. Sources: {', '.join(sources[:6])}. " + " | ".join(summary_bits)
    article_ids = [a.get("id") for a in cluster if a.get("id") is not None]
    uid = event_uid_from_seed(title, symbols, first_seen)
    return {
        "event_uid": uid,
        "title": title[:300],
        "summary": summary[:4000],
        "narrative": narr_text[:500],
        "symbols": symbols,
        "sentiment": round(avg_sent, 3),
        "importance": round(importance, 3),
        "confidence": conf,
        "source_count": len(sources),
        "headline_count": len(cluster),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "sources": sources,
        "freshness": round(fresh, 3),
        "article_ids": article_ids,
        "created_at": now,
        "updated_at": now,
    }


def load_n11_articles(conn: Any, *, since_ts: int, limit: int = 2000) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, published_at, source, title, summary, url, symbols, created_at
        FROM market_news_feed_n11
        WHERE published_at >= ? OR created_at >= ?
        ORDER BY published_at DESC, id DESC
        LIMIT ?
        """,
        (since_ts, since_ts, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r["id"],
            "published_at": int(r["published_at"] or 0),
            "created_at": int(r["created_at"] or 0),
            "timestamp": int(r["published_at"] or r["created_at"] or 0),
            "source": r["source"],
            "title": r["title"],
            "summary": r["summary"],
            "body": r["summary"],
            "url": r["url"],
            "symbols": r["symbols"],
        })
    return out


def load_all_source_articles(
    conn: Any, *, since_ts: int, limit: int = 2500,
) -> list[dict[str, Any]]:
    """Merge RSS/Telegram/X feed rows with Polymarket + Macro snapshots."""
    articles = load_n11_articles(conn, since_ts=since_ts, limit=limit)
    try:
        from bot.research.market_events.signal_intelligence.multi_source.polymarket_collector import (
            load_recent_polymarket_as_articles,
        )
        articles.extend(load_recent_polymarket_as_articles(conn, since_ts=since_ts))
    except Exception:
        logger.exception("failed loading polymarket articles for event merge")
    try:
        from bot.research.market_events.signal_intelligence.multi_source.macro_collector import (
            load_recent_macro_as_articles,
        )
        articles.extend(load_recent_macro_as_articles(conn, since_ts=since_ts))
    except Exception:
        logger.exception("failed loading macro articles for event merge")
    return articles


def upsert_events(conn: Any, events: list[dict[str, Any]]) -> dict[str, int]:
    created = 0
    merged = 0
    for ev in events:
        existing = conn.execute(
            """
            SELECT id, first_seen, headline_count
            FROM market_intel_events WHERE event_uid=?
            """,
            (ev["event_uid"],),
        ).fetchone()
        if existing:
            first_seen = min(int(existing["first_seen"] or ev["first_seen"]), int(ev["first_seen"]))
            execute_with_retry(
                conn,
                """
                UPDATE market_intel_events SET
                  updated_at=?, title=?, summary=?, narrative=?, symbols_json=?,
                  sentiment=?, importance=?, confidence=?, source_count=?,
                  headline_count=?, first_seen=?, last_seen=?,
                  sources_json=?, freshness=?, article_ids_json=?
                WHERE event_uid=?
                """,
                (
                    int(ev["updated_at"]),
                    ev["title"],
                    ev["summary"],
                    ev["narrative"],
                    json.dumps(ev.get("symbols") or [], ensure_ascii=False),
                    float(ev["sentiment"]),
                    float(ev["importance"]),
                    float(ev["confidence"]),
                    int(ev["source_count"]),
                    int(ev["headline_count"]),
                    first_seen,
                    int(ev["last_seen"]),
                    json.dumps(ev.get("sources") or [], ensure_ascii=False),
                    float(ev.get("freshness") or 0),
                    json.dumps(ev.get("article_ids") or [], ensure_ascii=False),
                    ev["event_uid"],
                ),
            )
            merged += 1
        else:
            execute_with_retry(
                conn,
                """
                INSERT INTO market_intel_events (
                  event_uid, created_at, updated_at, title, summary, narrative,
                  symbols_json, sentiment, importance, confidence,
                  source_count, headline_count, first_seen, last_seen,
                  sources_json, freshness, article_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev["event_uid"],
                    int(ev["created_at"]),
                    int(ev["updated_at"]),
                    ev["title"],
                    ev["summary"],
                    ev["narrative"],
                    json.dumps(ev.get("symbols") or [], ensure_ascii=False),
                    float(ev["sentiment"]),
                    float(ev["importance"]),
                    float(ev["confidence"]),
                    int(ev["source_count"]),
                    int(ev["headline_count"]),
                    int(ev["first_seen"]),
                    int(ev["last_seen"]),
                    json.dumps(ev.get("sources") or [], ensure_ascii=False),
                    float(ev.get("freshness") or 0),
                    json.dumps(ev.get("article_ids") or [], ensure_ascii=False),
                ),
            )
            created += 1
    return {"created": created, "merged": merged}


def run_event_engine_cycle_s43(
    *,
    lookback_sec: int = LOOKBACK_SEC,
    now: int | None = None,
    articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Cluster articles → upsert market_intel_events."""
    now_ts = int(now if now is not None else _utc_now())
    since = now_ts - int(lookback_sec)
    t0 = time.perf_counter()

    with market_events_connection() as conn:
        if articles is None:
            raw = load_all_source_articles(conn, since_ts=since)
        else:
            raw = articles
        clusters = cluster_articles(raw)
        dupes = int(getattr(cluster_articles, "last_duplicates_removed", 0) or 0)
        events = [build_event_from_cluster(c, now=now_ts) for c in clusters if c]
        # Rank: freshness * confidence * importance — newest dominate
        events.sort(
            key=lambda e: (
                float(e["freshness"]) * float(e["confidence"]) * (0.5 + float(e["importance"]))
            ),
            reverse=True,
        )
        stats = upsert_events(conn, events)
        conn.commit()

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result = {
        "articles": len(raw),
        "clusters": len(clusters),
        "events": len(events),
        "created": stats["created"],
        "merged": stats["merged"],
        "duplicates_removed": dupes,
        "elapsed_ms": round(elapsed_ms, 1),
        "now": now_ts,
    }
    logger.info(
        "s43 event-engine articles=%s clusters=%s created=%s merged=%s "
        "duplicates_removed=%s elapsed_ms=%.1f",
        result["articles"],
        result["clusters"],
        result["created"],
        result["merged"],
        result["duplicates_removed"],
        result["elapsed_ms"],
    )
    return result


def load_recent_events(
    conn: Any,
    *,
    since_ts: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, event_uid, created_at, updated_at, title, summary, narrative,
               symbols_json, sentiment, importance, confidence, source_count,
               headline_count, first_seen, last_seen, sources_json, freshness
        FROM market_intel_events
        WHERE last_seen >= ? OR updated_at >= ? OR created_at >= ? OR first_seen >= ?
        ORDER BY (freshness * confidence * (0.5 + importance)) DESC, last_seen DESC
        LIMIT ?
        """,
        (since_ts, since_ts, since_ts, since_ts, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            symbols = json.loads(r["symbols_json"] or "[]")
        except Exception:
            symbols = []
        try:
            sources = json.loads(r["sources_json"] or "[]")
        except Exception:
            sources = []
        out.append({
            "id": r["id"],
            "event_uid": r["event_uid"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "title": r["title"],
            "summary": r["summary"],
            "narrative": r["narrative"],
            "symbols": symbols if isinstance(symbols, list) else [],
            "sentiment": float(r["sentiment"] or 0),
            "importance": float(r["importance"] or 0),
            "confidence": float(r["confidence"] or 0),
            "source_count": int(r["source_count"] or 0),
            "headline_count": int(r["headline_count"] or 0),
            "first_seen": int(r["first_seen"] or 0),
            "last_seen": int(r["last_seen"] or 0),
            "sources": sources if isinstance(sources, list) else [],
            "freshness": float(r["freshness"] or 0),
        })
    return out
