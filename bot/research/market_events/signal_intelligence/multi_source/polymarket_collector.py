"""S44 Polymarket probability collector (Gamma API)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import execute_with_retry, market_events_connection
from bot.research.market_events.signal_intelligence.multi_source.config_loader import (
    enabled_sources,
)
from bot.research.market_events.signal_intelligence.multi_source.health import (
    record_source_health,
)
from bot.research.market_events.signal_intelligence.multi_source.http_util import (
    http_get_json,
    quote,
)

logger = logging.getLogger(__name__)
LOG_PATH = BASE_DIR / "logs" / "collector-polymarket.log"
GAMMA = "https://gamma-api.polymarket.com"


def _configure_log() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if any(
        isinstance(h, logging.FileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == LOG_PATH.resolve()
        for h in logger.handlers
    ):
        return
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)


def _extract_probability(market: dict[str, Any]) -> float | None:
    # outcomePrices often JSON string "[\"0.55\", \"0.45\"]"
    raw = market.get("outcomePrices") or market.get("outcome_prices")
    if isinstance(raw, str):
        try:
            prices = json.loads(raw)
        except Exception:
            prices = None
    else:
        prices = raw
    if isinstance(prices, list) and prices:
        try:
            return float(prices[0])
        except Exception:
            return None
    if market.get("lastTradePrice") is not None:
        try:
            return float(market["lastTradePrice"])
        except Exception:
            return None
    return None


def _search_markets(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    url = f"{GAMMA}/public-search?q={quote(query)}"
    try:
        data = http_get_json(url, timeout=12.0)
        if isinstance(data, dict):
            markets = data.get("markets") or data.get("events") or []
            if isinstance(markets, list):
                return markets[:limit]
        if isinstance(data, list):
            return data[:limit]
    except Exception:
        # Fallback: markets endpoint with search-ish filter
        url2 = f"{GAMMA}/markets?limit={limit}&active=true&closed=false"
        data2 = http_get_json(url2, timeout=12.0)
        if isinstance(data2, list):
            q = query.lower()
            return [m for m in data2 if q in str(m.get("question") or "").lower()][:limit]
    return []


def collect_polymarket_s44() -> dict[str, Any]:
    _configure_log()
    sources = enabled_sources("polymarket_sources")
    inserted = 0
    errors = 0
    per_source: dict[str, Any] = {}
    now = int(time.time())

    with market_events_connection() as conn:
        for row in sources:
            name = str(row.get("name") or "polymarket")
            query = str(row.get("query") or name)
            t0 = time.perf_counter()
            try:
                markets = _search_markets(query, limit=3)
                best = markets[0] if markets else None
                prob = _extract_probability(best) if best else None
                question = str((best or {}).get("question") or query)[:500]
                condition_id = str(
                    (best or {}).get("conditionId")
                    or (best or {}).get("id")
                    or ""
                )[:120]
                execute_with_retry(
                    conn,
                    """
                    INSERT INTO market_polymarket_signals (
                      created_at, name, query, question, probability,
                      condition_id, tags_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        name,
                        query,
                        question,
                        float(prob) if prob is not None else None,
                        condition_id,
                        json.dumps(row.get("tags") or [], ensure_ascii=False),
                        json.dumps(best or {}, ensure_ascii=False, default=str)[:8000],
                    ),
                )
                inserted += 1
                conn.commit()
                latency = (time.perf_counter() - t0) * 1000.0
                status = "ok" if best else "empty"
                record_source_health(
                    source_type="polymarket",
                    source_name=name,
                    status=status,
                    latency_ms=latency,
                    items=1 if best else 0,
                )
                per_source[name] = {
                    "ok": True,
                    "probability": prob,
                    "question": question[:120],
                }
                logger.info("polymarket %s prob=%s q=%s", name, prob, question[:80])
            except Exception as exc:
                errors += 1
                latency = (time.perf_counter() - t0) * 1000.0
                record_source_health(
                    source_type="polymarket",
                    source_name=name,
                    status="error",
                    error=str(exc)[:500],
                    latency_ms=latency,
                )
                per_source[name] = {"ok": False, "error": str(exc)[:200]}
                logger.warning("polymarket %s failed: %s", name, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass

    return {
        "source_type": "polymarket",
        "markets": len(sources),
        "inserted": inserted,
        "errors": errors,
        "per_source": per_source,
    }


def load_recent_polymarket_as_articles(
    conn: Any, *, since_ts: int, limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, created_at, name, query, question, probability, tags_json
        FROM market_polymarket_signals
        WHERE created_at >= ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (since_ts, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        prob = r["probability"]
        title = f"Polymarket {r['name']}: {r['question']}"
        summary = (
            f"Polymarket probability {prob:.3f}" if prob is not None
            else f"Polymarket signal for {r['name']}"
        )
        out.append({
            "id": f"poly-{r['id']}",
            "title": title[:300],
            "summary": summary,
            "body": summary,
            "source": "Polymarket",
            "source_type": "polymarket",
            "timestamp": int(r["created_at"] or 0),
            "published_at": int(r["created_at"] or 0),
            "symbols": [],
        })
    return out
