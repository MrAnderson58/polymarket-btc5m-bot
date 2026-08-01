"""Signal Evolution Engine V1 orchestrator (research-only)."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.signal_evolution_v1.decay import (
    edge_decay_profile,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.drift import (
    detect_drift,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.library import (
    load_evolution,
    upsert_evolution,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.lifecycle import (
    classify_lifecycle,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.ranking import (
    promotion_recommendation,
    score_signal,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.regimes import (
    regime_adaptation,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.rolling import (
    rolling_edge,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.survival import (
    survival_probability,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.report import (
    write_artifacts,
)


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _parse_json(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            obj = json.loads(v)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _load_occurrences(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: dict[str, Any] = {"source": "empty"}
    occ: list[dict[str, Any]] = []

    # 1) S40 signals joined to paper outcomes when available
    try:
        # Prefer outcomes-linked rows; cap scan for continuous research updates.
        s40_limit = int(limit) if limit else 5000
        sql = f"""
        SELECT s.signal_type AS signal, s.signal_id, s.symbol, s.direction,
               s.timestamp AS ts,
               s.snapshot_funding AS funding,
               s.snapshot_atr AS atr,
               s.snapshot_fear_greed AS fear_greed,
               s.snapshot_news_score AS news_score,
               s.snapshot_decision_confidence AS confidence,
               p.pnl_pct AS pnl,
               p.status AS status,
               COALESCE(f.market_regime, '') AS regime,
               f.gate_decision AS gate
        FROM market_events_signal_learning_s40_signals s
        LEFT JOIN market_events_paper_trades_s42 p
          ON p.s40_signal_type = s.signal_type AND p.s40_signal_id = s.signal_id
        LEFT JOIN market_events_trade_features_s55 f
          ON f.paper_trade_id = p.id
        WHERE p.pnl_pct IS NOT NULL OR s.timestamp IS NOT NULL
        ORDER BY s.timestamp DESC
        LIMIT {s40_limit}
        """
        rows = conn.execute(sql).fetchall()
        for r in rows:
            d = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else {}
            if not d:
                continue
            pnl = _safe_float(d.get("pnl"))
            # Keep signals even without pnl (unevaluated); evolution prefers evaluated
            occ.append({
                "signal": f"s40:{d.get('signal')}",
                "ts": float(d.get("ts") or 0),
                "pnl": pnl,
                "symbol": d.get("symbol"),
                "direction": d.get("direction"),
                "regime": d.get("regime") or "RANGE",
                "funding": _safe_float(d.get("funding")),
                "atr": _safe_float(d.get("atr")),
                "fear_greed": _safe_float(d.get("fear_greed")),
                "news_score": _safe_float(d.get("news_score")),
                "confidence": _safe_float(d.get("confidence")),
                "gate": d.get("gate"),
            })
        if occ:
            stats = {"source": "s40+s42", "n_raw": len(occ)}
    except Exception as exc:
        stats["s40_error"] = str(exc)

    # 2) Research lake composites (always add for breadth)
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        if research_lake_row_count(conn) > 0:
            rows = load_research_lake_rows(conn, limit=limit)
            for t in rows:
                feats = t.get("features") if isinstance(t.get("features"), dict) else _parse_json(t.get("features_json"))
                macro = t.get("macro") if isinstance(t.get("macro"), dict) else _parse_json(t.get("macro_json"))
                news = t.get("news") if isinstance(t.get("news"), dict) else _parse_json(t.get("news_json"))
                patterns = t.get("patterns") if isinstance(t.get("patterns"), dict) else _parse_json(t.get("patterns_json"))
                g31 = patterns.get("g31") if isinstance(patterns.get("g31"), dict) else {}
                regime = str(t.get("regime") or "RANGE")
                direction = str(t.get("direction") or "")
                symbol = str(t.get("symbol") or "")
                pnl = _safe_float(t.get("pnl") if t.get("pnl") is not None else t.get("pnl_pct"))
                ts = float(t.get("closed_at") or t.get("opened_at") or t.get("ts") or 0)
                base = {
                    "ts": ts,
                    "pnl": pnl,
                    "symbol": symbol,
                    "direction": direction,
                    "regime": regime,
                    "funding": _safe_float(feats.get("funding") or macro.get("funding")),
                    "atr_pct": _safe_float(feats.get("atr_pct") or feats.get("atr")),
                    "atr": _safe_float(feats.get("atr")),
                    "oi_delta": _safe_float(feats.get("oi_delta")),
                    "fear_greed": _safe_float(feats.get("fear_greed") or macro.get("fear_greed")),
                    "news_score": _safe_float(news.get("news_score")),
                    "ai_score": _safe_float(news.get("ai_score")),
                    "rsi": _safe_float(feats.get("rsi")),
                    "confidence": _safe_float(t.get("confidence") or g31.get("confidence")),
                }
                keys = [
                    f"lake:{symbol}|{direction}|{regime}",
                    f"lake:regime:{regime}|{direction}",
                ]
                if g31.get("id") is not None:
                    keys.append(f"g31_family:{direction}")
                for k in keys:
                    occ.append({**base, "signal": k})
            stats = {**stats, "lake_rows": len(rows), "source": stats.get("source", "lake") + "+lake"}
    except Exception as exc:
        stats["lake_error"] = str(exc)

    # 3) Edge library rules as named signals (match by attaching lake pnls in regime buckets)
    try:
        from bot.research.market_events.signal_intelligence.edge_discovery_v3.library import (
            load_library,
        )

        edges = load_library(conn, limit=200)
        # Create synthetic occurrences from edge metrics if no trade-level match
        now = time.time()
        for i, e in enumerate(edges):
            rule = str(e.get("rule") or e.get("id") or f"edge_{i}")[:120]
            n = int(e.get("sample") or e.get("n") or 0)
            ev = _safe_float(e.get("expectancy"))
            wr = _safe_float(e.get("wr") or e.get("winrate")) or 0.5
            if n <= 0 or ev is None:
                continue
            # Expand to pseudo-occurrences for evolution math
            m = min(n, 200)
            win_pnl = abs(ev) * 1.5 if ev != 0 else 0.5
            loss_pnl = -abs(ev) * 1.5 if ev != 0 else -0.5
            for j in range(m):
                pnl = win_pnl if ((j + 1) / m) <= wr else loss_pnl
                occ.append({
                    "signal": f"edge:{rule}",
                    "ts": now - (m - j) * 86400.0,
                    "pnl": pnl,
                    "regime": str(e.get("regime") or "RANGE"),
                    "direction": "LONG",
                    "atr_pct": 1.0,
                })
        stats["edges"] = len(edges)
    except Exception as exc:
        stats["edge_error"] = str(exc)

    # Keep evaluated occurrences preferentially
    evaluated = [o for o in occ if o.get("pnl") is not None]
    if not evaluated:
        return occ, stats
    # Cap total for runtime if huge
    if limit and len(evaluated) > limit * 3:
        evaluated = sorted(evaluated, key=lambda o: float(o.get("ts") or 0), reverse=True)[: limit * 3]
    stats["n_occurrences"] = len(evaluated)
    return evaluated, stats


def update_signal(occurrences: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
    """Update one signal from its occurrences. Target <500ms."""
    t0 = time.perf_counter()
    ordered = sorted(occurrences, key=lambda o: float(o.get("ts") or 0))
    pnls = [float(o["pnl"]) for o in ordered if o.get("pnl") is not None]
    ts = [float(o.get("ts") or 0) for o in ordered if o.get("pnl") is not None]
    signal = str((occurrences[0] or {}).get("signal") or "unknown")
    if not pnls:
        return {
            "signal": signal,
            "score": 0.0,
            "age": 0.0,
            "half_life": None,
            "drift": 0.0,
            "confidence": 0.05,
            "status": "BIRTH",
            "update_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }
    now = float(now if now is not None else (ts[-1] if ts else time.time()))
    age_days = (now - float(ts[0])) / 86400.0 if ts else 0.0
    rolling = rolling_edge(pnls)
    decay = edge_decay_profile(ts, pnls, now=now)
    survival = survival_probability(ts, pnls)
    regimes = regime_adaptation(ordered)
    drift = detect_drift(ordered)
    status = classify_lifecycle(
        n=len(pnls),
        age_days=age_days,
        rolling=rolling,
        decay=decay,
        survival=survival,
    )
    scored = score_signal(
        rolling=rolling, decay=decay, survival=survival, drift=drift, status=status
    )
    return {
        "signal": signal,
        "score": scored["score"],
        "age": round(age_days, 2),
        "half_life": decay.get("half_life_days"),
        "drift": drift.get("drift_score"),
        "confidence": scored["confidence"],
        "status": status,
        "rolling": rolling,
        "decay": decay,
        "survival": survival,
        "regimes": regimes,
        "drift_detail": drift,
        "n": len(pnls),
        "update_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


def run_signal_evolution_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist_library: bool = True,
    limit: int | None = None,
    min_occurrences: int = 5,
) -> dict[str, Any]:
    t0 = time.time()
    occurrences, load_stats = _load_occurrences(conn, limit=limit)
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in occurrences:
        by_signal[str(o.get("signal") or "unknown")].append(o)

    ranked: list[dict[str, Any]] = []
    update_ms: list[float] = []
    for sig, rows in by_signal.items():
        if len([r for r in rows if r.get("pnl") is not None]) < min_occurrences:
            continue
        rec = update_signal(rows)
        ranked.append(rec)
        update_ms.append(float(rec.get("update_ms") or 0.0))

    ranked.sort(key=lambda r: (-float(r.get("score") or 0), r.get("signal") or ""))
    lifecycle_counts = dict(Counter(r.get("status") for r in ranked))
    drift_scores = [float(r.get("drift") or 0) for r in ranked]
    half_lives = [float(r["half_life"]) for r in ranked if r.get("half_life") is not None]
    drift_stats = {
        "n_signals": len(ranked),
        "mean_drift": round(float(np.mean(drift_scores)), 4) if drift_scores else None,
        "high_drift": sum(1 for r in ranked if (r.get("drift_detail") or {}).get("drift_level") == "HIGH"),
        "med_drift": sum(1 for r in ranked if (r.get("drift_detail") or {}).get("drift_level") == "MED"),
        "low_drift": sum(1 for r in ranked if (r.get("drift_detail") or {}).get("drift_level") == "LOW"),
        "mean_half_life_days": round(float(np.mean(half_lives)), 2) if half_lives else None,
        "median_half_life_days": round(float(np.median(half_lives)), 2) if half_lives else None,
    }
    promotion = promotion_recommendation(ranked)
    strongest = ranked[:10]
    weakest = list(reversed(ranked[-10:])) if ranked else []

    library_upserted = 0
    if persist_library and ranked:
        library_upserted = upsert_evolution(conn, ranked)

    mean_ms = round(float(np.mean(update_ms)), 3) if update_ms else None
    p95_ms = None
    if update_ms:
        xs = sorted(update_ms)
        p95_ms = round(xs[int(0.95 * (len(xs) - 1))], 3)

    result: dict[str, Any] = {
        "ok": True,
        "n_signals": len(ranked),
        "n_occurrences": len(occurrences),
        "n_signal_keys": len(by_signal),
        "load_stats": load_stats,
        "ranked": ranked,
        "strongest": strongest,
        "weakest": weakest,
        "lifecycle_counts": lifecycle_counts,
        "drift_stats": drift_stats,
        "half_life_estimates": [
            {"signal": r.get("signal"), "half_life": r.get("half_life"), "slope": (r.get("decay") or {}).get("slope")}
            for r in ranked if r.get("half_life") is not None
        ][:30],
        "promotion": promotion,
        "mean_update_ms": mean_ms,
        "p95_update_ms": p95_ms,
        "within_budget": bool(mean_ms is not None and mean_ms < 500.0),
        "library_upserted": library_upserted,
        "library_sample": load_evolution(conn, limit=20) if persist_library else [],
        "elapsed_sec": round(time.time() - t0, 3),
        "research_only": True,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "optimizer_unchanged": True,
    }

    paths: dict[str, str] = {}
    md = ""
    if write_reports:
        paths = write_artifacts(result)
        try:
            from pathlib import Path

            md = Path(paths["report_md"]).read_text(encoding="utf-8")
        except Exception:
            md = ""
    result["paths"] = paths
    result["report_markdown"] = md
    return result


__all__ = ["run_signal_evolution_v1", "update_signal"]
