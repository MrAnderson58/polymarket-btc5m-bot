"""S57 — Market Regime Intelligence.

Classify market regime before each paper entry, then (optionally) allow/deny
using regime×direction statistics. LLM only answers fixed analyst questions and
writes WAITING_APPROVAL suggestions — never auto-applies strategy changes.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from bot.research.market_events.db import execute_with_retry
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

_FEATURES = "market_events_trade_features_s55"
_SNAP = "market_events_trade_snapshots_s56"
_RUNS = "market_events_regime_runs_s57"
_SUGGEST = "market_events_rule_suggestions_s56"
_OPS = "market_events_regime_ops_s57"

REGIME_STRONG_BULL = "STRONG_BULL"
REGIME_WEAK_BULL = "WEAK_BULL"
REGIME_RANGE = "RANGE"
REGIME_WEAK_BEAR = "WEAK_BEAR"
REGIME_STRONG_BEAR = "STRONG_BEAR"

REGIME_LABELS: dict[str, str] = {
    REGIME_STRONG_BULL: "Strong Bull",
    REGIME_WEAK_BULL: "Weak Bull",
    REGIME_RANGE: "Range",
    REGIME_WEAK_BEAR: "Weak Bear",
    REGIME_STRONG_BEAR: "Strong Bear",
}

REGIME_ORDER = (
    REGIME_STRONG_BULL,
    REGIME_WEAK_BULL,
    REGIME_RANGE,
    REGIME_WEAK_BEAR,
    REGIME_STRONG_BEAR,
)

# Persisted on every open so audits can track classifier revisions.
MARKET_REGIME_VERSION = "s57_v1"

GATE_REGIME_BLOCK = "REGIME_BLOCK"
GATE_REGIME_PASS = "REGIME_PASS"
GATE_REGIME_COLD = "REGIME_COLD"
GATE_REGIME_DISABLED = "REGIME_DISABLED"
GATE_REGIME_EXPLORE = "REGIME_EXPLORE"  # ε-greedy pass despite negative stats

STATUS_WAITING = "WAITING_APPROVAL"

S57_ENABLED = True
S57_FILTER_ENABLED = True
S57_MIN_EVIDENCE = 30
S57_MIN_EXPECTANCY = 0.0
S57_EXPLORATION_RATE = 0.10  # ε-greedy: 10% of blocked candidates pass for stats refresh
S57_LLM_ENABLED = False
S57_SUGGESTIONS_ENABLED = False
S57_SYMBOL_TOP_N = 5

# BTC return (% over lookback) → regime score component
_BTC_STRONG = 1.5
_BTC_WEAK = 0.4


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_s57_config_from_env() -> None:
    global S57_ENABLED, S57_FILTER_ENABLED, S57_MIN_EVIDENCE, S57_MIN_EXPECTANCY
    global S57_EXPLORATION_RATE
    global S57_LLM_ENABLED, S57_SUGGESTIONS_ENABLED, S57_SYMBOL_TOP_N
    if "S57_ENABLED" in os.environ:
        S57_ENABLED = _env_bool("S57_ENABLED", True)
    if "S57_FILTER_ENABLED" in os.environ:
        S57_FILTER_ENABLED = _env_bool("S57_FILTER_ENABLED", True)
    if "S57_MIN_EVIDENCE" in os.environ:
        try:
            S57_MIN_EVIDENCE = max(5, int(os.environ["S57_MIN_EVIDENCE"]))
        except (TypeError, ValueError):
            pass
    if "S57_MIN_EXPECTANCY" in os.environ:
        try:
            S57_MIN_EXPECTANCY = float(os.environ["S57_MIN_EXPECTANCY"])
        except (TypeError, ValueError):
            pass
    if "S57_EXPLORATION_RATE" in os.environ:
        try:
            S57_EXPLORATION_RATE = max(0.0, min(1.0, float(os.environ["S57_EXPLORATION_RATE"])))
        except (TypeError, ValueError):
            pass
    if "S57_LLM_ENABLED" in os.environ:
        S57_LLM_ENABLED = _env_bool("S57_LLM_ENABLED", False)
    if "S57_SUGGESTIONS_ENABLED" in os.environ:
        S57_SUGGESTIONS_ENABLED = _env_bool("S57_SUGGESTIONS_ENABLED", False)
    if "S57_SYMBOL_TOP_N" in os.environ:
        try:
            S57_SYMBOL_TOP_N = max(1, int(os.environ["S57_SYMBOL_TOP_N"]))
        except (TypeError, ValueError):
            pass


def _apply_defaults() -> None:
    global S57_ENABLED, S57_FILTER_ENABLED, S57_MIN_EVIDENCE, S57_MIN_EXPECTANCY
    global S57_EXPLORATION_RATE
    global S57_LLM_ENABLED, S57_SUGGESTIONS_ENABLED, S57_SYMBOL_TOP_N
    S57_ENABLED = _env_bool("S57_ENABLED", True)
    S57_FILTER_ENABLED = _env_bool("S57_FILTER_ENABLED", True)
    try:
        S57_MIN_EVIDENCE = max(5, int(os.environ.get("S57_MIN_EVIDENCE", "30")))
    except (TypeError, ValueError):
        S57_MIN_EVIDENCE = 30
    try:
        S57_MIN_EXPECTANCY = float(os.environ.get("S57_MIN_EXPECTANCY", "0"))
    except (TypeError, ValueError):
        S57_MIN_EXPECTANCY = 0.0
    try:
        S57_EXPLORATION_RATE = max(0.0, min(1.0, float(os.environ.get("S57_EXPLORATION_RATE", "0.1"))))
    except (TypeError, ValueError):
        S57_EXPLORATION_RATE = 0.10
    S57_LLM_ENABLED = _env_bool("S57_LLM_ENABLED", False)
    S57_SUGGESTIONS_ENABLED = _env_bool("S57_SUGGESTIONS_ENABLED", False)
    try:
        S57_SYMBOL_TOP_N = max(1, int(os.environ.get("S57_SYMBOL_TOP_N", "5")))
    except (TypeError, ValueError):
        S57_SYMBOL_TOP_N = 5


_apply_defaults()


def _row(r: Any) -> dict[str, Any]:
    if r is None:
        return {}
    try:
        return dict(r)
    except Exception:
        return {}


@dataclass(frozen=True)
class RegimeClassification:
    regime: str
    label: str
    score: float
    confidence: float
    btc_return_pct: float | None
    inputs: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _score_from_btc_return(btc_return_pct: float | None) -> float:
    if btc_return_pct is None:
        return 0.0
    if btc_return_pct >= _BTC_STRONG:
        return 2.0
    if btc_return_pct >= _BTC_WEAK:
        return 1.0
    if btc_return_pct <= -_BTC_STRONG:
        return -2.0
    if btc_return_pct <= -_BTC_WEAK:
        return -1.0
    return 0.0


def _score_from_trend(trend: float | None) -> float:
    if trend is None:
        return 0.0
    # trend often in [-1, 1] or small percent — clamp soft contribution
    if abs(trend) <= 1.5:
        if trend >= 0.35:
            return 0.5
        if trend <= -0.35:
            return -0.5
        return 0.0
    # treat as percent-like
    if trend >= 0.4:
        return 0.5
    if trend <= -0.4:
        return -0.5
    return 0.0


def _score_from_fear_greed(fear: float | None) -> float:
    if fear is None:
        return 0.0
    if fear >= 70:
        return 0.35
    if fear >= 55:
        return 0.15
    if fear <= 30:
        return -0.35
    if fear <= 45:
        return -0.15
    return 0.0


def _score_from_funding(funding: float | None) -> float:
    """Crowded longs (high +funding) slightly lean bearish for continuation."""
    if funding is None:
        return 0.0
    if funding >= 0.0005:
        return -0.2
    if funding <= -0.0005:
        return 0.2
    return 0.0


def _regime_from_score(score: float) -> str:
    if score >= 1.5:
        return REGIME_STRONG_BULL
    if score >= 0.5:
        return REGIME_WEAK_BULL
    if score <= -1.5:
        return REGIME_STRONG_BEAR
    if score <= -0.5:
        return REGIME_WEAK_BEAR
    return REGIME_RANGE


def classify_market_regime(
    *,
    btc_return_pct: float | None = None,
    trend: float | None = None,
    fear_greed: float | None = None,
    funding: float | None = None,
) -> RegimeClassification:
    """Deterministic 5-regime classifier from available market signals."""
    parts = {
        "btc": _score_from_btc_return(btc_return_pct),
        "trend": _score_from_trend(trend),
        "fear_greed": _score_from_fear_greed(fear_greed),
        "funding": _score_from_funding(funding),
    }
    present = sum(1 for k, v in parts.items() if v != 0.0 or (
        k == "btc" and btc_return_pct is not None
    ) or (k == "trend" and trend is not None) or (
        k == "fear_greed" and fear_greed is not None
    ) or (k == "funding" and funding is not None))
    # Prefer BTC return as primary weight
    score = (
        parts["btc"] * 1.0
        + parts["trend"] * 0.7
        + parts["fear_greed"] * 0.5
        + parts["funding"] * 0.4
    )
    regime = _regime_from_score(score)
    confidence = min(0.95, 0.35 + 0.15 * present + (0.2 if btc_return_pct is not None else 0.0))
    return RegimeClassification(
        regime=regime,
        label=REGIME_LABELS[regime],
        score=round(score, 4),
        confidence=round(confidence, 3),
        btc_return_pct=None if btc_return_pct is None else round(btc_return_pct, 4),
        inputs={
            "btc_return_pct": btc_return_pct,
            "trend": trend,
            "fear_greed": fear_greed,
            "funding": funding,
            "component_scores": parts,
        },
    )


def btc_return_pct_from_conn(
    conn: Any,
    *,
    bars: int = 12,
    as_of_ts: int | None = None,
) -> float | None:
    """BTC % return over recent 5m bars (default ~1h)."""
    try:
        from bot.research.market_events.signal_intelligence.candles import load_recent_candles
        candles = load_recent_candles(
            conn, symbol="BTC", venue="binance_futures", timeframe="5m", limit=bars + 2,
        )
        if as_of_ts is not None and candles:
            candles = [c for c in candles if int(c.open_ts) <= int(as_of_ts)]
        if len(candles) < 2 or candles[0].close <= 0:
            return None
        # use last `bars` span if available
        start = candles[-(bars + 1)].close if len(candles) > bars else candles[0].close
        end = candles[-1].close
        if start <= 0:
            return None
        return (end / start - 1.0) * 100.0
    except Exception:
        return None


def classify_for_entry(conn: Any, features: dict[str, Any]) -> RegimeClassification:
    """Classify using features + live BTC return when available."""
    refresh_s57_config_from_env()
    btc = btc_return_pct_from_conn(conn)
    return classify_market_regime(
        btc_return_pct=btc,
        trend=_safe_float(features.get("trend")),
        fear_greed=_safe_float(features.get("fear_greed")),
        funding=_safe_float(features.get("funding")),
    )


def attach_regime_to_features(conn: Any, features: dict[str, Any]) -> dict[str, Any]:
    """Mutate/return features with market_regime filled (S57)."""
    refresh_s57_config_from_env()
    if not S57_ENABLED:
        return features
    clf = classify_for_entry(conn, features)
    features["market_regime"] = clf.regime
    features["regime_score"] = clf.score
    features["regime_confidence"] = clf.confidence
    features["regime_btc_return_pct"] = clf.btc_return_pct
    features["market_regime_version"] = MARKET_REGIME_VERSION
    # Keep features_json in sync if present
    try:
        payload = json.loads(features.get("features_json") or "{}")
        if not isinstance(payload, dict):
            payload = {}
        payload["market_regime"] = clf.regime
        payload["regime_score"] = clf.score
        payload["regime_confidence"] = clf.confidence
        payload["regime_btc_return_pct"] = clf.btc_return_pct
        payload["regime_inputs"] = clf.inputs
        payload["market_regime_version"] = MARKET_REGIME_VERSION
        features["features_json"] = json.dumps(payload, default=str)
    except Exception:
        pass
    return features


def bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0, "pnl": 0.0, "winrate": None, "expectancy": None,
            "pf": None, "pf_inf": False, "avg_win": None, "avg_loss": None,
            "wins": 0, "losses": 0,
        }
    pnls = [float(r.get("pnl_usd") or 0.0) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 1e-12:
        pf: float | None = gross_win / gross_loss
        pf_inf = False
    elif gross_win > 0:
        pf = None
        pf_inf = True
    else:
        pf = 0.0
        pf_inf = False
    return {
        "n": n,
        "pnl": round(sum(pnls), 4),
        "winrate": round(100.0 * len(wins) / n, 2),
        "expectancy": round(sum(pnls) / n, 4),
        "pf": None if pf_inf else (round(pf, 4) if pf is not None else None),
        "pf_inf": pf_inf,
        "avg_win": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else None,
        "wins": len(wins),
        "losses": len(losses),
    }


def _load_closed_with_regime(conn: Any) -> list[dict[str, Any]]:
    """Prefer S56 snapshots; fall back to S55 features."""
    rows: list[dict[str, Any]] = []
    try:
        snap = conn.execute(
            f"""
            SELECT symbol, direction, pnl_usd, pnl_pct, exit_reason,
                   market_regime, hour, funding, news_score, macro_score,
                   ai_score, expected_pnl_pct, trend, fear_greed, duration_sec
            FROM {_SNAP}
            WHERE pnl_usd IS NOT NULL
            """,
        ).fetchall()
        rows = [_row(r) for r in snap]
    except Exception:
        rows = []
    if rows:
        return rows
    try:
        feat = conn.execute(
            f"""
            SELECT symbol, direction, pnl_usd, pnl_pct, exit_reason,
                   market_regime, hour, funding, news_score, macro_score,
                   ai_score, gate_expected_pnl_pct AS expected_pnl_pct,
                   trend, fear_greed, duration_sec
            FROM {_FEATURES}
            WHERE pnl_usd IS NOT NULL AND result IS NOT NULL
            """,
        ).fetchall()
        return [_row(r) for r in feat]
    except Exception:
        return []


def _canon_regime(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "STRONG_BULL": REGIME_STRONG_BULL,
        "WEAK_BULL": REGIME_WEAK_BULL,
        "RANGE": REGIME_RANGE,
        "WEAK_BEAR": REGIME_WEAK_BEAR,
        "STRONG_BEAR": REGIME_STRONG_BEAR,
        "RISK_ON": REGIME_WEAK_BULL,
        "RISK_OFF": REGIME_WEAK_BEAR,
        "BULL": REGIME_WEAK_BULL,
        "BEAR": REGIME_WEAK_BEAR,
    }
    return aliases.get(s)


def classify_from_row_features(row: dict[str, Any]) -> str:
    clf = classify_market_regime(
        btc_return_pct=None,
        trend=_safe_float(row.get("trend")),
        fear_greed=_safe_float(row.get("fear_greed")),
        funding=_safe_float(row.get("funding")),
    )
    return clf.regime


def compute_regime_stats(conn: Any, *, symbol_top_n: int | None = None) -> dict[str, Any]:
    """Per-regime WR / expectancy / PF / LONG-SHORT / best-worst coins."""
    refresh_s57_config_from_env()
    top_n = int(symbol_top_n if symbol_top_n is not None else S57_SYMBOL_TOP_N)
    rows = _load_closed_with_regime(conn)
    by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing = 0
    for r in rows:
        reg = _canon_regime(r.get("market_regime"))
        if reg is None:
            # Infer from sparse features when historical rows lack regime
            if any(r.get(k) is not None for k in ("trend", "fear_greed", "funding")):
                reg = classify_from_row_features(r)
            else:
                missing += 1
                continue
        r = dict(r)
        r["market_regime"] = reg
        by_regime[reg].append(r)

    regimes_out: list[dict[str, Any]] = []
    for reg in REGIME_ORDER:
        bucket = by_regime.get(reg, [])
        overall = bucket_metrics(bucket)
        long_rows = [x for x in bucket if str(x.get("direction") or "").upper() == "LONG"]
        short_rows = [x for x in bucket if str(x.get("direction") or "").upper() == "SHORT"]
        by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for x in bucket:
            by_sym[str(x.get("symbol") or "?").upper()].append(x)
        sym_stats = [
            {"symbol": sym, **bucket_metrics(b)}
            for sym, b in by_sym.items()
        ]
        sym_stats.sort(key=lambda x: (x["pnl"], -x["n"]))
        best = sorted(sym_stats, key=lambda x: (-(x["pnl"] or 0), -x["n"]))[:top_n]
        worst = sorted(sym_stats, key=lambda x: ((x["pnl"] or 0), -x["n"]))[:top_n]

        dir_combos = []
        for direction, drows in (("LONG", long_rows), ("SHORT", short_rows)):
            m = bucket_metrics(drows)
            dir_combos.append({"direction": direction, **m})

        # symbol × direction within regime
        sym_dir: list[dict[str, Any]] = []
        by_sd: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for x in bucket:
            by_sd[(str(x.get("symbol") or "?").upper(), str(x.get("direction") or "?").upper())].append(x)
        for (sym, direction), b in sorted(by_sd.items()):
            sym_dir.append({"symbol": sym, "direction": direction, **bucket_metrics(b)})
        sym_dir.sort(key=lambda x: (x["pnl"], -x["n"]))

        regimes_out.append({
            "regime": reg,
            "label": REGIME_LABELS[reg],
            **overall,
            "long": bucket_metrics(long_rows),
            "short": bucket_metrics(short_rows),
            "directions": dir_combos,
            "best_symbols": best,
            "worst_symbols": worst,
            "symbol_direction": sym_dir[:40],
        })

    # Cross tabs: which directions / coins work in which regime
    findings: list[dict[str, Any]] = []
    for reg_block in regimes_out:
        for d in reg_block["directions"]:
            if int(d.get("n") or 0) < S57_MIN_EVIDENCE:
                continue
            exp = d.get("expectancy")
            if exp is None:
                continue
            findings.append({
                "kind": "regime_direction",
                "regime": reg_block["regime"],
                "label": reg_block["label"],
                "direction": d["direction"],
                "n": d["n"],
                "expectancy": exp,
                "winrate": d.get("winrate"),
                "pf": d.get("pf"),
                "pf_inf": d.get("pf_inf"),
                "works": exp > 0 and (d.get("pf_inf") or (d.get("pf") or 0) >= 1.0),
            })
        for s in reg_block["worst_symbols"][:3]:
            if int(s.get("n") or 0) < max(10, S57_MIN_EVIDENCE // 2):
                continue
            if (s.get("expectancy") or 0) < 0:
                findings.append({
                    "kind": "regime_symbol_weak",
                    "regime": reg_block["regime"],
                    "label": reg_block["label"],
                    "symbol": s["symbol"],
                    "n": s["n"],
                    "expectancy": s.get("expectancy"),
                    "winrate": s.get("winrate"),
                    "pnl": s.get("pnl"),
                })
        for s in reg_block["best_symbols"][:3]:
            if int(s.get("n") or 0) < max(10, S57_MIN_EVIDENCE // 2):
                continue
            if (s.get("expectancy") or 0) > 0:
                findings.append({
                    "kind": "regime_symbol_strong",
                    "regime": reg_block["regime"],
                    "label": reg_block["label"],
                    "symbol": s["symbol"],
                    "n": s["n"],
                    "expectancy": s.get("expectancy"),
                    "winrate": s.get("winrate"),
                    "pnl": s.get("pnl"),
                })

    coverage = {
        "closed_rows": len(rows),
        "with_regime": sum(len(v) for v in by_regime.values()),
        "missing_regime": missing,
        "regimes_present": sorted(by_regime.keys()),
    }
    return {
        "regimes": regimes_out,
        "findings": findings,
        "coverage": coverage,
        "overall": bucket_metrics(rows),
    }


def _iter_regime_stat_rows(conn: Any) -> list[dict[str, Any]]:
    """Load closed outcomes for regime×direction stats (snapshots preferred)."""
    rows: list[dict[str, Any]] = []
    try:
        snap = conn.execute(
            f"""
            SELECT pnl_usd, direction, market_regime, trend, fear_greed, funding
            FROM {_SNAP}
            WHERE pnl_usd IS NOT NULL
            """,
        ).fetchall()
        for r in snap:
            rows.append(_row(r))
    except Exception:
        pass
    return rows


def lookup_regime_direction_stats(
    conn: Any,
    regime: str | None,
    direction: str | None,
) -> dict[str, Any]:
    """Lightweight regime×direction metrics for the gate (no full report).

    S60: snapshot history lives on the research DB; live ``conn`` is only a
    fallback for features / non-separated installs.
    """
    reg = _canon_regime(regime)
    direction = str(direction or "").upper()
    empty = bucket_metrics([])
    if not reg or direction not in ("LONG", "SHORT"):
        return empty

    raw: list[dict[str, Any]] = []
    try:
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
            resolve_research_db_config,
        )

        if resolve_research_db_config().separated:
            with research_connection(readonly=True) as rconn:
                raw = _iter_regime_stat_rows(rconn)
        else:
            raw = _iter_regime_stat_rows(conn)
    except Exception:
        raw = _iter_regime_stat_rows(conn)

    rows: list[dict[str, Any]] = []
    for row in raw:
        rreg = _canon_regime(row.get("market_regime"))
        if rreg is None and any(row.get(k) is not None for k in ("trend", "fear_greed", "funding")):
            rreg = classify_from_row_features(row)
        if rreg != reg:
            continue
        if str(row.get("direction") or "").upper() != direction:
            continue
        rows.append(row)

    if not rows:
        try:
            feat = conn.execute(
                f"""
                SELECT pnl_usd, direction, market_regime, trend, fear_greed, funding
                FROM {_FEATURES}
                WHERE pnl_usd IS NOT NULL AND result IS NOT NULL
                """,
            ).fetchall()
            for r in feat:
                row = _row(r)
                rreg = _canon_regime(row.get("market_regime"))
                if rreg is None and any(row.get(k) is not None for k in ("trend", "fear_greed", "funding")):
                    rreg = classify_from_row_features(row)
                if rreg != reg:
                    continue
                if str(row.get("direction") or "").upper() != direction:
                    continue
                rows.append(row)
        except Exception:
            return empty
    return bucket_metrics(rows)


def apply_regime_gate(
    conn: Any,
    features: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    """Allow/deny using regime×direction historical expectancy when evidence exists."""
    refresh_s57_config_from_env()
    meta: dict[str, Any] = {
        "regime": features.get("market_regime"),
        "regime_score": features.get("regime_score"),
        "regime_confidence": features.get("regime_confidence"),
    }
    if not S57_ENABLED:
        return True, GATE_REGIME_DISABLED, meta
    if not features.get("market_regime"):
        attach_regime_to_features(conn, features)
        meta["regime"] = features.get("market_regime")
        meta["regime_score"] = features.get("regime_score")
        meta["regime_confidence"] = features.get("regime_confidence")

    if not S57_FILTER_ENABLED:
        return True, GATE_REGIME_PASS, meta

    stats = lookup_regime_direction_stats(
        conn, features.get("market_regime"), features.get("direction"),
    )
    meta["regime_dir_stats"] = stats
    n = int(stats.get("n") or 0)
    if n < S57_MIN_EVIDENCE:
        return True, GATE_REGIME_COLD, meta

    exp = stats.get("expectancy")
    pf = stats.get("pf")
    pf_inf = bool(stats.get("pf_inf"))
    bad_exp = exp is not None and float(exp) < float(S57_MIN_EXPECTANCY)
    bad_pf = (not pf_inf) and pf is not None and float(pf) < 1.0
    if bad_exp and bad_pf:
        # ε-greedy exploration: allow a fraction of trades through to refresh stats
        import random
        if S57_EXPLORATION_RATE > 0 and random.random() < S57_EXPLORATION_RATE:
            meta["exploration"] = True
            logger.info("s57 regime explore pass (ε=%.2f): regime=%s dir=%s n=%d exp=%.4f pf=%.4f",
                        S57_EXPLORATION_RATE, features.get("market_regime"),
                        features.get("direction"), n, float(exp or 0), float(pf or 0))
            return True, GATE_REGIME_EXPLORE, meta
        return False, GATE_REGIME_BLOCK, meta
    return True, GATE_REGIME_PASS, meta


def backfill_regimes(conn: Any, *, limit: int | None = None) -> dict[str, Any]:
    """Fill NULL market_regime on snapshots/features via classifier."""
    refresh_s57_config_from_env()
    updated_snap = 0
    updated_feat = 0

    sql_snap = f"""
        SELECT id, trend, fear_greed, funding, market_regime
        FROM {_SNAP}
        WHERE market_regime IS NULL OR TRIM(market_regime) = ''
        ORDER BY id DESC
    """
    params: tuple = ()
    if limit is not None and limit > 0:
        sql_snap += " LIMIT ?"
        params = (int(limit),)
    try:
        for r in conn.execute(sql_snap, params).fetchall():
            row = _row(r)
            reg = classify_from_row_features(row)
            execute_with_retry(
                conn,
                f"UPDATE {_SNAP} SET market_regime = ? WHERE id = ?",
                (reg, int(row["id"])),
            )
            updated_snap += 1
    except Exception as exc:
        logger.warning("s57 backfill snapshots failed: %s", exc)

    sql_feat = f"""
        SELECT id, trend, fear_greed, funding, market_regime
        FROM {_FEATURES}
        WHERE market_regime IS NULL OR TRIM(market_regime) = ''
        ORDER BY id DESC
    """
    params_f: tuple = ()
    if limit is not None and limit > 0:
        sql_feat += " LIMIT ?"
        params_f = (int(limit),)
    try:
        for r in conn.execute(sql_feat, params_f).fetchall():
            row = _row(r)
            reg = classify_from_row_features(row)
            execute_with_retry(
                conn,
                f"UPDATE {_FEATURES} SET market_regime = ? WHERE id = ?",
                (reg, int(row["id"])),
            )
            updated_feat += 1
    except Exception as exc:
        logger.warning("s57 backfill features failed: %s", exc)

    return {
        "ok": True,
        "updated_snapshots": updated_snap,
        "updated_features": updated_feat,
    }


def _fmt_pf(m: dict[str, Any]) -> str:
    if m.get("pf_inf"):
        return "inf"
    if m.get("pf") is None:
        return "—"
    return f"{float(m['pf']):.2f}"


def _fmt_money(v: Any) -> str:
    if v is None:
        return "—"
    return f"${float(v):+.2f}"


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v):.1f}%"


def format_regime_report(conn: Any) -> str:
    refresh_s57_config_from_env()
    stats = compute_regime_stats(conn)
    cov = stats.get("coverage") or {}
    lines = [
        "S57 Market Regime Intelligence",
        f"  enabled={S57_ENABLED}  filter={S57_FILTER_ENABLED}  "
        f"min_evidence={S57_MIN_EVIDENCE}  llm={S57_LLM_ENABLED}  "
        f"suggestions={S57_SUGGESTIONS_ENABLED}",
        f"  closed={cov.get('closed_rows', 0)}  with_regime={cov.get('with_regime', 0)}  "
        f"missing={cov.get('missing_regime', 0)}",
        f"  Overall: n={stats['overall'].get('n', 0)}  "
        f"PnL={_fmt_money(stats['overall'].get('pnl'))}  "
        f"WR={_fmt_pct(stats['overall'].get('winrate'))}  "
        f"E={_fmt_money(stats['overall'].get('expectancy'))}  "
        f"PF={_fmt_pf(stats['overall'])}",
        "",
        "Regimes",
        f"  {'Regime':<14} {'n':>5} {'PnL':>10} {'WR':>7} {'E':>9} {'PF':>6}  "
        f"{'LONG E':>9} {'SHORT E':>9}",
    ]
    for block in stats.get("regimes") or []:
        lines.append(
            f"  {block['label']:<14} {block.get('n'):>5} "
            f"{_fmt_money(block.get('pnl')):>10} {_fmt_pct(block.get('winrate')):>7} "
            f"{_fmt_money(block.get('expectancy')):>9} {_fmt_pf(block):>6}  "
            f"{_fmt_money((block.get('long') or {}).get('expectancy')):>9} "
            f"{_fmt_money((block.get('short') or {}).get('expectancy')):>9}"
        )
        best = ", ".join(
            f"{s['symbol']}({_fmt_money(s.get('pnl'))})"
            for s in (block.get("best_symbols") or [])[:3]
        ) or "—"
        worst = ", ".join(
            f"{s['symbol']}({_fmt_money(s.get('pnl'))})"
            for s in (block.get("worst_symbols") or [])[:3]
        ) or "—"
        lines.append(f"    best: {best}")
        lines.append(f"    worst: {worst}")

    lines.extend(["", "Statistical findings (regime × direction / symbols)"])
    findings = stats.get("findings") or []
    if not findings:
        lines.append("  (insufficient evidence — need more closed trades with regime)")
    for f in findings[:40]:
        if f.get("kind") == "regime_direction":
            flag = "WORKS" if f.get("works") else "WEAK"
            lines.append(
                f"  [{flag}] {f.get('label')} {f.get('direction')}: "
                f"n={f.get('n')} E={_fmt_money(f.get('expectancy'))} "
                f"WR={_fmt_pct(f.get('winrate'))} PF={_fmt_pf(f)}"
            )
        elif f.get("kind") == "regime_symbol_weak":
            lines.append(
                f"  [WEAK COIN] {f.get('label')} {f.get('symbol')}: "
                f"n={f.get('n')} E={_fmt_money(f.get('expectancy'))} "
                f"PnL={_fmt_money(f.get('pnl'))}"
            )
        elif f.get("kind") == "regime_symbol_strong":
            lines.append(
                f"  [STRONG COIN] {f.get('label')} {f.get('symbol')}: "
                f"n={f.get('n')} E={_fmt_money(f.get('expectancy'))} "
                f"PnL={_fmt_money(f.get('pnl'))}"
            )

    lines.extend([
        "",
        "LLM answers only: distinguishing features / filters to test / confidence.",
        "No automatic strategy changes. Approve via approve-suggestion.",
        "Backfill: python -m bot.research.market_events market-regime --backfill",
    ])
    return "\n".join(lines)


def format_s57_report_block(conn: Any) -> list[str]:
    refresh_s57_config_from_env()
    stats = compute_regime_stats(conn)
    cov = stats.get("coverage") or {}
    lines = [
        "",
        "S57 Market Regime",
        f"  enabled={S57_ENABLED} filter={S57_FILTER_ENABLED} "
        f"closed={cov.get('closed_rows', 0)} with_regime={cov.get('with_regime', 0)} "
        f"missing={cov.get('missing_regime', 0)}",
    ]
    for block in stats.get("regimes") or []:
        if int(block.get("n") or 0) == 0:
            continue
        lines.append(
            f"  {block['label']}: n={block.get('n')} "
            f"WR={_fmt_pct(block.get('winrate'))} "
            f"E={_fmt_money(block.get('expectancy'))} "
            f"PF={_fmt_pf(block)} "
            f"L={_fmt_money((block.get('long') or {}).get('expectancy'))} "
            f"S={_fmt_money((block.get('short') or {}).get('expectancy'))}"
        )
    lines.append("  Full: python -m bot.research.market_events market-regime")
    return lines


_LLM_SYSTEM = """You are a trading statistics analyst for a crypto paper-trading bot.
Answer ONLY these three questions based on the provided regime report:
1) Which features most strongly distinguish profitable trades?
2) Which filters are worth testing (regime × direction / symbol)?
3) How high is your confidence (0-100) and why?

Rules:
- Do NOT invent strategy code changes.
- Do NOT instruct the bot to auto-apply anything.
- Be specific, cite n / expectancy / PF from the data.
- Propose at most 5 concrete filter hypotheses as bullet points.
"""


def _deterministic_llm_answers(stats: dict[str, Any]) -> str:
    lines = [
        "1) Distinguishing features (deterministic)",
    ]
    findings = stats.get("findings") or []
    works = [f for f in findings if f.get("kind") == "regime_direction" and f.get("works")]
    weak = [f for f in findings if f.get("kind") == "regime_direction" and not f.get("works")]
    if works:
        for f in works[:5]:
            lines.append(
                f"  - {f.get('label')} {f.get('direction')} works: "
                f"n={f.get('n')} E={f.get('expectancy')} WR={f.get('winrate')}"
            )
    else:
        lines.append("  - insufficient profitable regime×direction contrasts")
    lines.append("2) Filters worth testing")
    if weak:
        for f in weak[:5]:
            lines.append(
                f"  - Block {f.get('direction')} in {f.get('label')} "
                f"(n={f.get('n')} E={f.get('expectancy')} PF={f.get('pf')})"
            )
    else:
        lines.append("  - collect more closed trades with regime labels first")
    for f in findings:
        if f.get("kind") == "regime_symbol_weak":
            lines.append(
                f"  - Reduce {f.get('symbol')} in {f.get('label')} "
                f"(n={f.get('n')} E={f.get('expectancy')})"
            )
    cov = stats.get("coverage") or {}
    with_r = int(cov.get("with_regime") or 0)
    conf = 20
    if with_r >= 500:
        conf = 70
    elif with_r >= 200:
        conf = 55
    elif with_r >= 80:
        conf = 40
    lines.append(f"3) Confidence: {conf}/100 (with_regime={with_r}, missing={cov.get('missing_regime')})")
    lines.append("No automatic strategy changes.")
    return "\n".join(lines)


def _call_llm_regime(stats: dict[str, Any]) -> tuple[str, str]:
    refresh_s57_config_from_env()
    report = json.dumps({
        "coverage": stats.get("coverage"),
        "regimes": [
            {
                "label": b.get("label"),
                "n": b.get("n"),
                "winrate": b.get("winrate"),
                "expectancy": b.get("expectancy"),
                "pf": b.get("pf"),
                "long": b.get("long"),
                "short": b.get("short"),
                "best_symbols": b.get("best_symbols"),
                "worst_symbols": b.get("worst_symbols"),
            }
            for b in (stats.get("regimes") or [])
        ],
        "findings": (stats.get("findings") or [])[:40],
    }, default=str)
    det = _deterministic_llm_answers(stats)
    if not S57_LLM_ENABLED:
        return det, "deterministic"
    try:
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
            claude_call_allowed,
            telegram_claude_session,
        )
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            call_claude_g2,
            is_claude_configured,
        )
        if not is_claude_configured():
            return "Claude not configured.\n\n" + det, "deterministic_fallback"
        ok, reason = claude_call_allowed()
        if not ok:
            return f"Claude blocked ({reason}).\n\n{det}", "deterministic_fallback"
        with telegram_claude_session():
            resp = call_claude_g2(
                system=_LLM_SYSTEM,
                user_content=f"Regime statistics JSON:\n{report}",
                label="s57_market_regime",
                max_tokens=2048,
            )
        return resp.text.strip(), "claude"
    except Exception as exc:
        logger.warning("s57 LLM failed: %s", exc)
        return f"Claude error: {exc}\n\n{det}", "deterministic_fallback"


def _suggestions_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not S57_SUGGESTIONS_ENABLED:
        return out
    for f in findings:
        if f.get("kind") == "regime_direction" and not f.get("works"):
            if int(f.get("n") or 0) < S57_MIN_EVIDENCE:
                continue
            out.append({
                "rule_text": (
                    f"Test filter: disable {f.get('direction')} in {f.get('label')} "
                    f"(n={f.get('n')}, E={f.get('expectancy')}, PF={f.get('pf')})"
                ),
                "evidence_trades": int(f.get("n") or 0),
                "expected_improvement_pct": 1.0,
                "confidence_pct": min(90.0, 40.0 + float(f.get("n") or 0) / 10.0),
                "source": "s57_regime_stats",
                "evidence_json": json.dumps(f, default=str),
            })
        if f.get("kind") == "regime_symbol_weak":
            out.append({
                "rule_text": (
                    f"Test filter: reduce {f.get('symbol')} size/weight in {f.get('label')} "
                    f"(n={f.get('n')}, E={f.get('expectancy')})"
                ),
                "evidence_trades": int(f.get("n") or 0),
                "expected_improvement_pct": 0.8,
                "confidence_pct": min(85.0, 35.0 + float(f.get("n") or 0) / 10.0),
                "source": "s57_regime_stats",
                "evidence_json": json.dumps(f, default=str),
            })
    return out[:8]


def run_regime_analysis(
    conn: Any,
    *,
    now: int | None = None,
    with_llm: bool = False,
    write_suggestions: bool | None = None,
) -> dict[str, Any]:
    """Compute stats, optional LLM Q&A, optional WAITING_APPROVAL suggestions."""
    refresh_s57_config_from_env()
    now = int(now if now is not None else time.time())
    stats = compute_regime_stats(conn)
    llm_text, method = ("", "skipped")
    if with_llm or S57_LLM_ENABLED:
        llm_text, method = _call_llm_regime(stats)
    else:
        llm_text, method = _deterministic_llm_answers(stats), "deterministic"

    cur = execute_with_retry(
        conn,
        f"""
        INSERT INTO {_RUNS} (report_json, llm_text, llm_method, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (json.dumps(stats, default=str), llm_text, method, now),
    )
    run_id = None
    try:
        run_id = int(cur.lastrowid)
    except Exception:
        try:
            run_id = int(conn.execute(f"SELECT MAX(id) AS i FROM {_RUNS}").fetchone()["i"])
        except Exception:
            run_id = None

    do_suggest = S57_SUGGESTIONS_ENABLED if write_suggestions is None else bool(write_suggestions)
    suggestions: list[dict[str, Any]] = []
    if do_suggest:
        suggestions = _suggestions_from_findings(stats.get("findings") or [])
        for s in suggestions:
            execute_with_retry(
                conn,
                f"""
                INSERT INTO {_SUGGEST} (
                  run_id, rule_text, evidence_json, evidence_trades,
                  expected_improvement_pct, confidence_pct, status, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    s["rule_text"],
                    s.get("evidence_json"),
                    int(s.get("evidence_trades") or 0),
                    float(s.get("expected_improvement_pct") or 0),
                    float(s.get("confidence_pct") or 0),
                    STATUS_WAITING,
                    s.get("source") or "s57_regime_stats",
                    now,
                ),
            )

    try:
        execute_with_retry(
            conn,
            f"""
            INSERT INTO {_OPS} (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            ("last_regime_run_id", str(run_id or 0), now),
        )
    except Exception:
        pass

    return {
        "run_id": run_id,
        "stats": stats,
        "llm_method": method,
        "llm_text": llm_text,
        "suggestions_created": len(suggestions),
        "suggestions_enabled": do_suggest,
    }


def doctor_s57_status(conn: Any) -> dict[str, Any]:
    refresh_s57_config_from_env()
    stats = compute_regime_stats(conn)
    cov = stats.get("coverage") or {}
    last = None
    try:
        last = conn.execute(f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        pass
    last_txt = "—"
    if last:
        last_txt = f"#{last['id']} method={last['llm_method']}"
    return {
        "enabled": S57_ENABLED,
        "filter": S57_FILTER_ENABLED,
        "closed": cov.get("closed_rows", 0),
        "with_regime": cov.get("with_regime", 0),
        "missing": cov.get("missing_regime", 0),
        "last_run": last_txt,
        "ok": True,
    }


__all__ = [
    "GATE_REGIME_BLOCK",
    "GATE_REGIME_COLD",
    "GATE_REGIME_DISABLED",
    "GATE_REGIME_PASS",
    "MARKET_REGIME_VERSION",
    "REGIME_LABELS",
    "REGIME_ORDER",
    "apply_regime_gate",
    "attach_regime_to_features",
    "backfill_regimes",
    "classify_for_entry",
    "classify_market_regime",
    "compute_regime_stats",
    "doctor_s57_status",
    "format_regime_report",
    "format_s57_report_block",
    "refresh_s57_config_from_env",
    "run_regime_analysis",
]
