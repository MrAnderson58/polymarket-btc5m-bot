"""S50/S51 — Intelligence compression: normalize → multi-key dedup → cluster → score → top events.

Machine filters the firehose; the LLM receives only a ranked short list.
Full event records remain in SQLite for learning / validation / debug.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from bot.research.market_events.signal_intelligence.event_intelligence.similarity import (
    normalize_text,
    significant_tokens,
    title_similarity,
)

DEFAULT_TOP_LIMIT = 8
DEDUP_RATIO = 0.88
CLUSTER_SIM_THRESHOLD = 0.72

_IMPACT_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


@dataclass
class CompressionStats:
    raw_count: int = 0
    normalized_count: int = 0
    deduped_count: int = 0
    clustered_count: int = 0
    top_count: int = 0
    synthetic_added: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "raw_count": self.raw_count,
            "normalized_count": self.normalized_count,
            "deduped_count": self.deduped_count,
            "clustered_count": self.clustered_count,
            "top_count": self.top_count,
            "synthetic_added": self.synthetic_added,
        }


def _parse_symbols(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(s).upper() for s in raw if s]
    if isinstance(raw, str):
        if raw.strip().startswith("["):
            try:
                import json
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(s).upper() for s in parsed if s]
            except Exception:
                pass
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return []


def _parse_entities(raw: Any) -> set[str]:
    if isinstance(raw, set):
        return {str(x).lower() for x in raw if x}
    if isinstance(raw, list):
        return {str(x).lower() for x in raw if x}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(x).lower() for x in parsed if x}
        except Exception:
            return {t.strip().lower() for t in raw.split(",") if t.strip()}
    return set()


def text_hash(title: str, *, body: str = "", url: str = "") -> str:
    """Stable content fingerprint for dedup."""
    blob = "|".join([
        normalize_text(title),
        normalize_text(body)[:400],
        normalize_text(url),
    ])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def normalize_event(event: dict[str, Any], *, now: int | None = None) -> dict[str, Any] | None:
    """Canonical event dict for compression (title required)."""
    title = str(event.get("title") or event.get("headline") or "").strip()
    if not title:
        return None
    now_ts = int(now if now is not None else time.time())
    norm_title = normalize_text(title)
    body = str(event.get("summary") or event.get("body") or event.get("why_it_matters") or "")
    url = str(event.get("url") or event.get("link") or "").strip().lower()
    tokens = significant_tokens(title + " " + body[:200])
    symbols = _parse_symbols(event.get("symbols") or event.get("symbols_json"))
    entities = _parse_entities(event.get("entities") or event.get("entities_json"))
    if not entities and symbols:
        entities = {s.lower() for s in symbols}
    narrative = str(event.get("narrative") or "").strip().lower()
    impact = str(event.get("market_impact") or event.get("impact") or "MEDIUM").upper()
    if impact not in _IMPACT_RANK:
        impact = "MEDIUM"
    try:
        importance = float(event.get("importance") or 0.5)
    except (TypeError, ValueError):
        importance = 0.5
    try:
        confidence = float(event.get("confidence") or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    try:
        freshness = float(event.get("freshness") or 1.0)
    except (TypeError, ValueError):
        freshness = 1.0
    last_seen = int(event.get("last_seen") or event.get("updated_at") or event.get("created_at") or now_ts)
    source_count = int(event.get("source_count") or 1)
    headline_count = int(event.get("headline_count") or 1)
    source_type = str(event.get("source_type") or event.get("kind") or "intel").lower()
    polarity = str(event.get("polarity") or "Neutral")
    sentiment = event.get("sentiment")
    cluster_id = str(
        event.get("cluster_id")
        or event.get("event_uid")
        or event.get("cluster_key")
        or ""
    ).strip()
    th = str(event.get("text_hash") or text_hash(title, body=body, url=url))
    return {
        "title": title[:200],
        "norm_title": norm_title,
        "url": url,
        "text_hash": th,
        "tokens": tokens,
        "entities": entities,
        "symbols": symbols,
        "narrative": narrative,
        "market_impact": impact,
        "importance": importance,
        "confidence": confidence,
        "freshness": freshness,
        "last_seen": last_seen,
        "source_count": max(1, source_count),
        "headline_count": max(1, headline_count),
        "source_type": source_type,
        "polarity": polarity,
        "sentiment": sentiment,
        "event_uid": event.get("event_uid"),
        "cluster_id": cluster_id,
        "cluster_size": int(event.get("cluster_size") or 1),
        "synthetic": bool(event.get("synthetic")),
        "_why_it_matters": event.get("why_it_matters"),
        "_confirmed_by": event.get("confirmed_by"),
    }


def _entity_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    S51 multi-key dedup:
      title similarity, URL, text_hash, entity overlap, cluster_id.
    Higher impact / importance wins.
    """
    ranked = sorted(
        events,
        key=lambda e: (
            _impact_rank_value(e),
            float(e.get("importance") or 0),
            int(e.get("last_seen") or 0),
        ),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    seen_titles: list[str] = []
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    seen_clusters: set[str] = set()
    seen_uids: set[str] = set()
    kept_entities: list[set[str]] = []

    for ev in ranked:
        uid = str(ev.get("event_uid") or "")
        if uid and uid in seen_uids:
            continue
        cluster_id = str(ev.get("cluster_id") or "")
        if cluster_id and cluster_id in seen_clusters:
            continue
        url = str(ev.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        th = str(ev.get("text_hash") or "")
        if th and th in seen_hashes:
            continue

        norm = ev.get("norm_title") or normalize_text(ev.get("title") or "")
        if not norm:
            continue
        ents = ev.get("entities") or set()
        dup = False
        for i, prev in enumerate(seen_titles):
            if norm == prev or SequenceMatcher(None, norm, prev).ratio() >= DEDUP_RATIO:
                dup = True
                break
            if ents and kept_entities[i]:
                if (
                    _entity_overlap(ents, kept_entities[i]) >= 0.66
                    and SequenceMatcher(None, norm, prev).ratio() >= 0.62
                ):
                    dup = True
                    break
        if dup:
            continue

        kept.append(ev)
        seen_titles.append(norm)
        kept_entities.append(ents if isinstance(ents, set) else set(ents or []))
        if uid:
            seen_uids.add(uid)
        if cluster_id:
            seen_clusters.add(cluster_id)
        if url:
            seen_urls.add(url)
        if th:
            seen_hashes.add(th)
    return kept


def _cluster_key(ev: dict[str, Any]) -> str:
    cid = ev.get("cluster_id") or ""
    if cid:
        return f"c:{cid}"
    narr = ev.get("narrative") or ""
    if narr:
        return f"n:{narr}"
    syms = ev.get("symbols") or []
    if syms:
        return f"s:{','.join(sorted(syms)[:2])}"
    tokens = sorted(ev.get("tokens") or [])[:4]
    return f"t:{'-'.join(tokens)}" if tokens else f"t:{ev.get('norm_title', '')[:40]}"


def _merge_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick representative title; aggregate counts."""
    best = max(
        cluster,
        key=lambda e: (
            _impact_rank_value(e),
            float(e.get("importance") or 0),
            int(e.get("source_count") or 0),
            int(e.get("last_seen") or 0),
        ),
    )
    merged = dict(best)
    merged["source_count"] = sum(int(e.get("source_count") or 1) for e in cluster)
    merged["headline_count"] = sum(int(e.get("headline_count") or 1) for e in cluster)
    merged["cluster_size"] = len(cluster)
    merged["importance"] = max(float(e.get("importance") or 0) for e in cluster)
    merged["confidence"] = max(float(e.get("confidence") or 0) for e in cluster)
    impacts = [_impact_rank_value(e) for e in cluster]
    merged["market_impact"] = _rank_to_impact(max(impacts))
    return merged


def _rank_to_impact(rank: int) -> str:
    for label, val in _IMPACT_RANK.items():
        if val == rank:
            return label
    return "MEDIUM"


def _impact_rank_value(ev: dict[str, Any]) -> int:
    return _IMPACT_RANK.get(str(ev.get("market_impact") or "MEDIUM").upper(), 2)


def cluster_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge identical / near-identical stories within thematic buckets."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        buckets.setdefault(_cluster_key(ev), []).append(ev)

    merged: list[dict[str, Any]] = []
    for bucket in buckets.values():
        if len(bucket) == 1:
            merged.append(bucket[0])
            continue
        # Greedy merge by title similarity inside bucket
        pool = sorted(bucket, key=lambda e: int(e.get("last_seen") or 0), reverse=True)
        clusters: list[list[dict[str, Any]]] = []
        for ev in pool:
            placed = False
            for cluster in clusters:
                rep = cluster[0]
                sim = title_similarity(ev.get("title") or "", rep.get("title") or "")
                tok_overlap = len(ev.get("tokens") or set()) & len(rep.get("tokens") or set())
                if sim >= CLUSTER_SIM_THRESHOLD or (
                    sim >= 0.55 and tok_overlap >= 2
                ):
                    cluster.append(ev)
                    placed = True
                    break
            if not placed:
                clusters.append([ev])
        for cluster in clusters:
            merged.append(_merge_cluster(cluster))
    return merged


def score_event(ev: dict[str, Any], *, now: int | None = None) -> float:
    """Higher = more important for trader + LLM attention."""
    now_ts = int(now if now is not None else time.time())
    freshness = float(ev.get("freshness") or 1.0)
    last_seen = int(ev.get("last_seen") or now_ts)
    age_h = max(0.0, (now_ts - last_seen) / 3600.0)
    recency = max(0.2, 1.0 - min(age_h / 48.0, 0.8))
    importance = float(ev.get("importance") or 0.5)
    confidence = float(ev.get("confidence") or 0.5)
    impact_mul = {4: 1.35, 3: 1.15, 2: 1.0, 1: 0.75}.get(_impact_rank_value(ev), 1.0)
    sources = min(6, int(ev.get("source_count") or 1))
    cluster_bonus = min(3, int(ev.get("cluster_size") or 1) - 1) * 0.05
    synthetic_bonus = 0.12 if ev.get("synthetic") else 0.0
    return round(
        freshness * recency * confidence * (0.45 + importance) * impact_mul
        + 0.08 * sources
        + cluster_bonus
        + synthetic_bonus,
        4,
    )


def rank_events(events: list[dict[str, Any]], *, now: int | None = None) -> list[dict[str, Any]]:
    scored = []
    for ev in events:
        row = dict(ev)
        row["compression_score"] = score_event(ev, now=now)
        scored.append(row)
    return sorted(
        scored,
        key=lambda e: (
            float(e.get("compression_score") or 0),
            _impact_rank_value(e),
            int(e.get("last_seen") or 0),
        ),
        reverse=True,
    )


def build_market_signal_events(
    *,
    etf: dict[str, Any] | None = None,
    funding: dict[str, Any] | None = None,
    whales: dict[str, Any] | None = None,
    macro: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Inject high-signal numeric / headline cues as pseudo-events for ranking."""
    out: list[dict[str, Any]] = []
    etf = etf or {}
    funding = funding or {}
    whales = whales or {}
    macro = macro or {}

    btc = etf.get("btc_etf") or {}
    nf1 = btc.get("netflow_1d")
    if nf1 is not None:
        try:
            val = float(nf1)
            sign = "+" if val >= 0 else ""
            title = f"ETF netflow 1d {sign}{val:.0f}M"
            impact = "HIGH" if abs(val) >= 200 else "MEDIUM"
            out.append({
                "title": title,
                "market_impact": impact,
                "importance": 0.85 if abs(val) >= 200 else 0.65,
                "confidence": 0.9,
                "freshness": 1.0,
                "source_type": "etf",
                "narrative": "etf",
                "symbols": ["BTC"],
                "polarity": "Bullish" if val > 0 else "Bearish",
                "synthetic": True,
            })
        except (TypeError, ValueError):
            pass

    if funding.get("extreme_funding"):
        cur = funding.get("current")
        title = f"Funding extreme ({cur})" if cur is not None else "Funding extreme"
        out.append({
            "title": title,
            "market_impact": "HIGH",
            "importance": 0.8,
            "confidence": 0.85,
            "freshness": 1.0,
            "source_type": "funding",
            "narrative": "funding",
            "symbols": ["BTC"],
            "polarity": "Neutral",
            "synthetic": True,
        })

    for row in (whales.get("largest_transfers") or [])[:2]:
        t = str(row.get("title") or row) if isinstance(row, dict) else str(row)
        if t.strip():
            out.append({
                "title": t[:160],
                "market_impact": "HIGH",
                "importance": 0.75,
                "confidence": 0.7,
                "freshness": 0.95,
                "source_type": "whale",
                "narrative": "whale",
                "symbols": ["BTC"],
                "polarity": "Bearish" if "out" in t.lower() else "Neutral",
                "synthetic": True,
            })

    for key, label in (("cpi", "CPI"), ("fed", "Fed")):
        block = macro.get(key) or {}
        headlines = block.get("headlines") or []
        for h in headlines[:1]:
            ht = str(h).strip()
            if not ht:
                continue
            out.append({
                "title": ht[:160],
                "market_impact": "HIGH" if key == "cpi" else "MEDIUM",
                "importance": 0.7,
                "confidence": 0.75,
                "freshness": 0.9,
                "source_type": "macro",
                "narrative": key,
                "symbols": ["BTC", "SPX"],
                "polarity": "Neutral",
                "synthetic": True,
            })
    return out


def to_llm_event(ev: dict[str, Any], *, rank: int) -> dict[str, Any]:
    """Minimal event shape for LLM context — no article bodies."""
    return {
        "rank": rank,
        "title": ev.get("title"),
        "impact": ev.get("market_impact"),
        "polarity": ev.get("polarity"),
        "narrative": ev.get("narrative") or "",
        "symbols": ev.get("symbols") or [],
        "source_count": ev.get("source_count"),
        "score": ev.get("compression_score"),
        "cluster_id": ev.get("cluster_id") or "",
    }


def format_top_events_text(events: list[dict[str, Any]]) -> str:
    """Human/LLM-friendly numbered list (matches trader terminal examples)."""
    lines = ["Top Events", ""]
    if not events:
        lines.append("(none)")
        return "\n".join(lines)
    for i, ev in enumerate(events, start=1):
        title = str(ev.get("title") or "—")
        impact = str(ev.get("impact") or ev.get("market_impact") or "MEDIUM")
        lines.append(f"{i}.")
        lines.append(title)
        lines.append(f"Impact {impact}")
        if i < len(events):
            lines.append("")
    return "\n".join(lines)


def compress_intelligence_for_llm(
    events: list[dict[str, Any]],
    *,
    market_signals: list[dict[str, Any]] | None = None,
    now: int | None = None,
    limit: int = DEFAULT_TOP_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Full S50 pipeline.
    Returns (llm_top_events, compression_metadata).
    """
    stats = CompressionStats(raw_count=len(events))
    now_ts = int(now if now is not None else time.time())

    normalized: list[dict[str, Any]] = []
    for raw in events:
        row = normalize_event(raw, now=now_ts)
        if row:
            normalized.append(row)
    stats.normalized_count = len(normalized)

    if market_signals:
        for sig in market_signals:
            row = normalize_event(sig, now=now_ts)
            if row:
                normalized.append(row)
        stats.synthetic_added = len(market_signals)

    deduped = dedupe_events(normalized)
    stats.deduped_count = len(deduped)

    clustered = cluster_events(deduped)
    stats.clustered_count = len(clustered)

    ranked = rank_events(clustered, now=now_ts)
    top = ranked[:limit]
    stats.top_count = len(top)

    llm_events = [to_llm_event(ev, rank=i + 1) for i, ev in enumerate(top)]
    meta = {
        "version": "s50",
        "stats": stats.to_dict(),
        "summary": format_top_events_text(llm_events),
    }
    return llm_events, meta


# Convenience alias
compress_events_for_analyst = compress_intelligence_for_llm
