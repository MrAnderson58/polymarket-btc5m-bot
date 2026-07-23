"""S56 — Trade Intelligence & LLM Post-Mortem (observe / suggest only).

Never auto-applies strategy changes. Suggestions stay WAITING_APPROVAL until a human
approves; approval only records status + Cursor task text (e.g. S56.1).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_SNAP = "market_events_trade_snapshots_s56"
_RUNS = "market_events_postmortem_runs_s56"
_SUGGEST = "market_events_rule_suggestions_s56"
_OPS = "market_events_postmortem_ops_s56"
_TRADES = "market_events_paper_trades_s42"
_FEATURES = "market_events_trade_features_s55"

STATUS_WAITING = "WAITING_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_APPLIED = "APPLIED"

S56_TOP_N = 500
S56_RCA_EVERY_N = 1000
S56_LLM_ENABLED = False
S56_MIN_EVIDENCE = 30

_NUMERIC_FEATURES = (
    "ai_score",
    "expected_pnl_pct",
    "funding",
    "oi_delta",
    "etf_flow",
    "fear_greed",
    "macro_score",
    "news_score",
    "market_score",
    "volatility",
    "atr",
    "volume",
    "trend",
    "vwap",
    "spread",
    "hour",
    "weekday",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_s56_config_from_env() -> None:
    global S56_TOP_N, S56_RCA_EVERY_N, S56_LLM_ENABLED, S56_MIN_EVIDENCE
    if "S56_TOP_N" in os.environ:
        try:
            S56_TOP_N = max(10, int(os.environ["S56_TOP_N"]))
        except (TypeError, ValueError):
            pass
    if "S56_RCA_EVERY_N" in os.environ:
        try:
            S56_RCA_EVERY_N = max(50, int(os.environ["S56_RCA_EVERY_N"]))
        except (TypeError, ValueError):
            pass
    if "S56_LLM_ENABLED" in os.environ:
        S56_LLM_ENABLED = _env_bool("S56_LLM_ENABLED", False)
    if "S56_MIN_EVIDENCE" in os.environ:
        try:
            S56_MIN_EVIDENCE = max(5, int(os.environ["S56_MIN_EVIDENCE"]))
        except (TypeError, ValueError):
            pass


def _apply_defaults() -> None:
    global S56_TOP_N, S56_RCA_EVERY_N, S56_LLM_ENABLED, S56_MIN_EVIDENCE
    try:
        S56_TOP_N = max(10, int(os.environ.get("S56_TOP_N", "500")))
    except (TypeError, ValueError):
        S56_TOP_N = 500
    try:
        S56_RCA_EVERY_N = max(50, int(os.environ.get("S56_RCA_EVERY_N", "1000")))
    except (TypeError, ValueError):
        S56_RCA_EVERY_N = 1000
    S56_LLM_ENABLED = _env_bool("S56_LLM_ENABLED", False)
    try:
        S56_MIN_EVIDENCE = max(5, int(os.environ.get("S56_MIN_EVIDENCE", "30")))
    except (TypeError, ValueError):
        S56_MIN_EVIDENCE = 30


_apply_defaults()


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _ops_get(conn: Any, key: str, default: str = "0") -> str:
    try:
        r = conn.execute(f"SELECT value FROM {_OPS} WHERE key = ?", (key,)).fetchone()
        return str(r["value"]) if r else default
    except Exception:
        return default


def _ops_set(conn: Any, key: str, value: str, now: int | None = None) -> None:
    now = int(now if now is not None else time.time())
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {_OPS} (key, value, updated_at)
        VALUES (?, ?, ?)
        """,
        (key, value, now),
    )


def closed_trade_count(conn: Any) -> int:
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status = 'CLOSED'",
            ).fetchone()["n"]
            or 0
        )
    except Exception:
        return 0


def record_close_snapshot(
    conn: Any,
    *,
    trade_row: Any,
    now: int | None = None,
) -> None:
    """Persist full close snapshot (Block 1). Sparse fields stay NULL."""
    now = int(now if now is not None else time.time())
    t = _row(trade_row)
    paper_id = int(t.get("id") or 0)
    s_type = str(t.get("s40_signal_type") or "")
    s_id = int(t.get("s40_signal_id") or 0)

    feat: dict[str, Any] = {}
    try:
        fr = conn.execute(
            f"""
            SELECT * FROM {_FEATURES}
            WHERE s40_signal_type = ? AND s40_signal_id = ?
            """,
            (s_type, s_id),
        ).fetchone()
        if fr:
            feat = _row(fr)
    except Exception:
        pass

    ts = int(t.get("created_at") or now)
    dt = datetime.fromtimestamp(ts)
    hour = feat.get("hour")
    weekday = feat.get("weekday")
    if hour is None:
        hour = dt.hour
    if weekday is None:
        weekday = dt.weekday()

    trailing = 1 if (
        int(t.get("trailing_active") or 0) == 1
        or str(t.get("exit_reason") or "").upper() == "TRAILING"
        or int(feat.get("trailing") or 0) == 1
    ) else 0

    snap = {
        "paper_trade_id": paper_id or None,
        "s40_signal_type": s_type,
        "s40_signal_id": s_id,
        "symbol": str(t.get("symbol") or feat.get("symbol") or ""),
        "direction": str(t.get("direction") or feat.get("direction") or "").upper(),
        "entry": _safe_float(t.get("entry")),
        "exit_price": _safe_float(t.get("exit_price")),
        "pnl_usd": _safe_float(t.get("pnl_usd")),
        "pnl_pct": _safe_float(t.get("pnl_pct")),
        "duration_sec": int(t.get("holding_seconds") or feat.get("duration_sec") or 0) or None,
        "exit_reason": t.get("exit_reason") or feat.get("exit_reason"),
        "tp1": _safe_float(t.get("tp1")),
        "tp2": _safe_float(t.get("tp2")),
        "trailing": trailing,
        "ai_score": _safe_float(feat.get("ai_score") if feat.get("ai_score") is not None else t.get("decision_confidence")),
        "expected_pnl_pct": _safe_float(feat.get("gate_expected_pnl_pct")),
        "funding": _safe_float(feat.get("funding")),
        "oi_delta": _safe_float(feat.get("oi_delta")),
        "etf_flow": _safe_float(feat.get("etf_flow")),
        "fear_greed": _safe_float(feat.get("fear_greed")),
        "macro_score": _safe_float(feat.get("macro_score")),
        "news_score": _safe_float(feat.get("news_score")),
        "market_score": _safe_float(feat.get("shock_score")),
        "volatility": _safe_float(feat.get("volatility")),
        "atr": _safe_float(feat.get("atr")),
        "volume": _safe_float(feat.get("volume")),
        "trend": _safe_float(feat.get("trend")),
        "vwap": None,
        "spread": _safe_float(feat.get("spread")),
        "timestamp": ts,
        "hour": int(hour) if hour is not None else None,
        "weekday": int(weekday) if weekday is not None else None,
        "market_regime": feat.get("market_regime"),
    }
    snap["snapshot_json"] = json.dumps(snap, default=str)

    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_SNAP} (
              paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
              entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
              tp1, tp2, trailing, ai_score, expected_pnl_pct,
              funding, oi_delta, etf_flow, fear_greed, macro_score, news_score,
              market_score, volatility, atr, volume, trend, vwap, spread,
              timestamp, hour, weekday, market_regime, snapshot_json, created_at
            ) VALUES (
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?
            )
            """,
            (
                snap["paper_trade_id"], snap["s40_signal_type"], snap["s40_signal_id"],
                snap["symbol"], snap["direction"],
                snap["entry"], snap["exit_price"], snap["pnl_usd"], snap["pnl_pct"],
                snap["duration_sec"], snap["exit_reason"],
                snap["tp1"], snap["tp2"], snap["trailing"], snap["ai_score"],
                snap["expected_pnl_pct"],
                snap["funding"], snap["oi_delta"], snap["etf_flow"], snap["fear_greed"],
                snap["macro_score"], snap["news_score"],
                snap["market_score"], snap["volatility"], snap["atr"], snap["volume"],
                snap["trend"], snap["vwap"], snap["spread"],
                snap["timestamp"], snap["hour"], snap["weekday"], snap["market_regime"],
                snap["snapshot_json"], now,
            ),
        )
    except Exception as exc:
        logger.warning("s56 record_close_snapshot failed: %s", exc)
        return

    try:
        maybe_run_postmortem(conn, now=now)
    except Exception as exc:
        logger.warning("s56 maybe_run_postmortem failed: %s", exc)


def top_winners(conn: Any, *, n: int | None = None) -> list[dict[str, Any]]:
    refresh_s56_config_from_env()
    n = int(n if n is not None else S56_TOP_N)
    rows = conn.execute(
        f"""
        SELECT * FROM {_SNAP}
        WHERE pnl_usd IS NOT NULL AND pnl_usd > 0
        ORDER BY pnl_usd DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    return [_row(r) for r in rows]


def top_losers(conn: Any, *, n: int | None = None) -> list[dict[str, Any]]:
    refresh_s56_config_from_env()
    n = int(n if n is not None else S56_TOP_N)
    rows = conn.execute(
        f"""
        SELECT * FROM {_SNAP}
        WHERE pnl_usd IS NOT NULL AND pnl_usd < 0
        ORDER BY pnl_usd ASC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    return [_row(r) for r in rows]


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5


def compute_feature_importance(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Effect-size ranking: |mean_w - mean_l| / pooled_std → stars."""
    out: list[dict[str, Any]] = []
    for feat in _NUMERIC_FEATURES:
        wv = [_safe_float(r.get(feat)) for r in winners]
        lv = [_safe_float(r.get(feat)) for r in losers]
        wv = [v for v in wv if v is not None]
        lv = [v for v in lv if v is not None]
        if len(wv) < 5 or len(lv) < 5:
            continue
        mw, ml = _mean(wv), _mean(lv)
        assert mw is not None and ml is not None
        pooled = ((_std(wv) ** 2 + _std(lv) ** 2) / 2) ** 0.5
        effect = abs(mw - ml) / pooled if pooled > 1e-12 else abs(mw - ml)
        if effect >= 0.8:
            stars = "+++++"
        elif effect >= 0.5:
            stars = "++++"
        elif effect >= 0.35:
            stars = "+++"
        elif effect >= 0.2:
            stars = "++"
        elif effect >= 0.1:
            stars = "+"
        else:
            stars = ""
        out.append({
            "feature": feat,
            "winners_avg": round(mw, 4),
            "losers_avg": round(ml, 4),
            "delta": round(mw - ml, 4),
            "effect_size": round(effect, 4),
            "stars": stars,
            "n_winners": len(wv),
            "n_losers": len(lv),
        })
    out.sort(key=lambda x: x["effect_size"], reverse=True)
    return out


def _rate(rows: list[dict[str, Any]], pred) -> tuple[float, int]:
    if not rows:
        return 0.0, 0
    hit = sum(1 for r in rows if pred(r))
    return 100.0 * hit / len(rows), hit


def compute_rca(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Automatic contrasts — numbers only."""
    findings: list[dict[str, Any]] = []

    def add(text: str, *, among: str, pct: float, n: int, base: int) -> None:
        if base < S56_MIN_EVIDENCE or pct < 55.0:
            return
        findings.append({
            "statement": text,
            "among": among,
            "pct": round(pct, 1),
            "n": n,
            "base": base,
        })

    # LONG + negative funding among losers
    long_losers = [r for r in losers if str(r.get("direction")) == "LONG"]
    if long_losers:
        pct, n = _rate(long_losers, lambda r: (_safe_float(r.get("funding")) or 0) < 0)
        add(
            f"{pct:.0f}% убыточных LONG были при отрицательном Funding",
            among="losers_LONG",
            pct=pct,
            n=n,
            base=len(long_losers),
        )

    short_winners = [r for r in winners if str(r.get("direction")) == "SHORT"]
    if short_winners:
        pct, n = _rate(short_winners, lambda r: (_safe_float(r.get("etf_flow")) or 0) > 0)
        add(
            f"{pct:.0f}% лучших SHORT были после положительного ETF flow",
            among="winners_SHORT",
            pct=pct,
            n=n,
            base=len(short_winners),
        )

    # Direction mix
    for among, rows, label in (
        ("winners", winners, "прибыльных"),
        ("losers", losers, "убыточных"),
    ):
        if not rows:
            continue
        long_pct, ln = _rate(rows, lambda r: str(r.get("direction")) == "LONG")
        add(
            f"{long_pct:.0f}% {label} сделок — LONG",
            among=among,
            pct=long_pct,
            n=ln,
            base=len(rows),
        )

    # Exit reasons among losers
    for reason in ("STOP", "TIMEOUT", "TRAILING", "TP1", "TP2"):
        pct, n = _rate(losers, lambda r, rr=reason: str(r.get("exit_reason") or "").upper() == rr)
        add(
            f"{pct:.0f}% убытков закрылись по {reason}",
            among="losers",
            pct=pct,
            n=n,
            base=len(losers),
        )

    # Hour / symbol concentration among losers
    by_sym: dict[str, int] = defaultdict(int)
    for r in losers:
        by_sym[str(r.get("symbol") or "?")] += 1
    if losers and by_sym:
        top_sym, top_n = max(by_sym.items(), key=lambda x: x[1])
        pct = 100.0 * top_n / len(losers)
        add(
            f"{pct:.0f}% убытков на {top_sym}",
            among="losers",
            pct=pct,
            n=top_n,
            base=len(losers),
        )

    by_hour: dict[int, int] = defaultdict(int)
    for r in losers:
        if r.get("hour") is not None:
            by_hour[int(r["hour"])] += 1
    if losers and by_hour:
        h, hn = max(by_hour.items(), key=lambda x: x[1])
        pct = 100.0 * hn / len(losers)
        add(
            f"{pct:.0f}% убытков в час {h}",
            among="losers",
            pct=pct,
            n=hn,
            base=len(losers),
        )

    # High AI score losers (overconfidence)
    hi_ai_losers = [r for r in losers if (_safe_float(r.get("ai_score")) or 0) >= 0.7]
    if len(losers) >= S56_MIN_EVIDENCE and hi_ai_losers:
        pct = 100.0 * len(hi_ai_losers) / len(losers)
        add(
            f"{pct:.0f}% убытков имели AI Score ≥ 0.7",
            among="losers",
            pct=pct,
            n=len(hi_ai_losers),
            base=len(losers),
        )

    findings.sort(key=lambda x: (-x["pct"], -x["n"]))
    return {
        "winners_n": len(winners),
        "losers_n": len(losers),
        "findings": findings[:25],
        "what_unites_winners": [f for f in findings if f["among"].startswith("winners")][:8],
        "what_unites_losers": [f for f in findings if "loser" in f["among"]][:8],
    }


def _suggest_from_stats(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
    importance: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Minimal rule proposals from numbers (no LLM). Never applied automatically."""
    suggestions: list[dict[str, Any]] = []
    refresh_s56_config_from_env()

    long_losers = [r for r in losers if str(r.get("direction")) == "LONG"]
    if len(long_losers) >= S56_MIN_EVIDENCE:
        neg_f = [r for r in long_losers if (_safe_float(r.get("funding")) or 0) < -0.0002]
        if len(neg_f) / len(long_losers) >= 0.55:
            # Counterfactual approx: drop those trades' avg pnl drag
            drag = abs(sum(float(r.get("pnl_usd") or 0) for r in neg_f) / max(1, len(losers)))
            suggestions.append({
                "rule_text": "Disable LONG when Funding < -0.02%",
                "evidence_trades": len(neg_f),
                "expected_improvement_pct": round(min(15.0, drag / 10.0), 2),
                "confidence_pct": round(min(95.0, 55.0 + 100.0 * len(neg_f) / len(long_losers)), 1),
                "source": "deterministic_rca",
                "evidence_json": json.dumps({
                    "long_losers": len(long_losers),
                    "long_losers_neg_funding": len(neg_f),
                    "threshold": -0.0002,
                }),
            })

    # Symbol night block if concentrated losses in hours 0-4
    night_losers = [
        r for r in losers
        if r.get("hour") is not None and 0 <= int(r["hour"]) <= 4
    ]
    if len(losers) >= S56_MIN_EVIDENCE and night_losers and len(night_losers) / len(losers) >= 0.35:
        by_sym: dict[str, int] = defaultdict(int)
        for r in night_losers:
            by_sym[str(r.get("symbol") or "?")] += 1
        sym, sn = max(by_sym.items(), key=lambda x: x[1])
        if sn >= S56_MIN_EVIDENCE // 2:
            suggestions.append({
                "rule_text": f"Do not trade {sym} at night (hour 0-4 UTC/local)",
                "evidence_trades": sn,
                "expected_improvement_pct": round(min(10.0, 100.0 * sn / len(losers) * 0.15), 2),
                "confidence_pct": round(min(92.0, 50.0 + 40.0 * sn / len(night_losers)), 1),
                "source": "deterministic_rca",
                "evidence_json": json.dumps({"symbol": sym, "night_losses": sn, "all_losers": len(losers)}),
            })

    # Top feature thresholds
    for imp in importance[:3]:
        if not imp.get("stars"):
            continue
        feat = imp["feature"]
        if feat == "funding" and imp["delta"] > 0:
            suggestions.append({
                "rule_text": "Prefer setups with Funding closer to winners' average (avoid deep negative funding)",
                "evidence_trades": imp["n_winners"] + imp["n_losers"],
                "expected_improvement_pct": round(min(8.0, imp["effect_size"] * 3), 2),
                "confidence_pct": round(min(90.0, 40.0 + imp["effect_size"] * 40), 1),
                "source": "feature_importance",
                "evidence_json": json.dumps(imp),
            })
        if feat == "news_score" and imp["delta"] > 0:
            suggestions.append({
                "rule_text": "Require higher News Score (winners avg exceeds losers)",
                "evidence_trades": imp["n_winners"] + imp["n_losers"],
                "expected_improvement_pct": round(min(8.0, imp["effect_size"] * 3), 2),
                "confidence_pct": round(min(90.0, 40.0 + imp["effect_size"] * 40), 1),
                "source": "feature_importance",
                "evidence_json": json.dumps(imp),
            })

    # Dedup by rule_text
    seen = set()
    uniq = []
    for s in suggestions:
        if s["rule_text"] in seen:
            continue
        seen.add(s["rule_text"])
        uniq.append(s)
    return uniq[:10]


_LLM_SYSTEM = """You are a hedge-fund trading analyst.
You do NOT invent narratives. You use ONLY the provided numbers.
You do NOT change the strategy. You only propose minimal rules.
Answer in this exact structure:

1. Profitable features (with %)
2. Losing features (with %)
3. Proposed rules (max 5, each: IF ... THEN ...; expected_improvement_pct; confidence_pct; evidence_trades)
4. Confidence overall (0-100)
5. Missing data needed

Rules must be minimal and falsifiable. No fluff."""


def _call_llm_postmortem(
    conn: Any,
    *,
    rca: dict[str, Any],
    importance: list[dict[str, Any]],
    winners_n: int,
    losers_n: int,
) -> tuple[str, str]:
    """Returns (text, method)."""
    refresh_s56_config_from_env()
    if not S56_LLM_ENABLED:
        return _format_deterministic_analyst(rca, importance), "deterministic"

    try:
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
            claude_call_allowed,
            telegram_claude_session,
        )
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            ClaudeClientError,
            call_claude_g2,
            is_claude_configured,
        )
    except Exception as exc:
        return f"LLM unavailable: {exc}\n\n" + _format_deterministic_analyst(rca, importance), "deterministic_fallback"

    if not is_claude_configured():
        return "Claude not configured.\n\n" + _format_deterministic_analyst(rca, importance), "deterministic_fallback"

    ok, reason = claude_call_allowed()
    if not ok:
        return (
            f"Claude blocked ({reason}). Using deterministic analyst only.\n\n"
            + _format_deterministic_analyst(rca, importance)
        ), "deterministic_fallback"

    user = json.dumps(
        {
            "winners_n": winners_n,
            "losers_n": losers_n,
            "rca": rca,
            "feature_importance": importance[:15],
            "instruction": "Find statistically meaningful differences. Numbers only.",
        },
        default=str,
        indent=2,
    )
    try:
        with telegram_claude_session():
            resp = call_claude_g2(
                system=_LLM_SYSTEM,
                user_content=user,
                label="s56_postmortem",
                max_tokens=2048,
            )
        return resp.text.strip(), "claude"
    except Exception as exc:
        logger.warning("s56 LLM failed: %s", exc)
        return f"Claude error: {exc}\n\n" + _format_deterministic_analyst(rca, importance), "deterministic_fallback"


def _format_deterministic_analyst(rca: dict[str, Any], importance: list[dict[str, Any]]) -> str:
    lines = [
        "1. Profitable features",
    ]
    for f in rca.get("what_unites_winners") or []:
        lines.append(f"  - {f['statement']} (n={f['n']}/{f['base']})")
    if not rca.get("what_unites_winners"):
        lines.append("  - insufficient evidence")
    lines.append("2. Losing features")
    for f in rca.get("what_unites_losers") or []:
        lines.append(f"  - {f['statement']} (n={f['n']}/{f['base']})")
    if not rca.get("what_unites_losers"):
        lines.append("  - insufficient evidence")
    lines.append("3. Proposed rules — see WAITING_APPROVAL suggestions (not auto-applied)")
    lines.append("4. Confidence overall — based on sample sizes only")
    lines.append(
        f"  winners={rca.get('winners_n')} losers={rca.get('losers_n')}"
    )
    lines.append("5. Missing data needed")
    missing = [x["feature"] for x in importance if x.get("n_winners", 0) < 20]
    sparse = [f for f in _NUMERIC_FEATURES if f not in {i["feature"] for i in importance}]
    lines.append(f"  - sparse/unused dims: {', '.join(sparse[:8]) or 'none'}")
    lines.append("Feature importance:")
    for i in importance[:10]:
        lines.append(
            f"  {i['feature']}: {i['stars'] or '·'} effect={i['effect_size']} "
            f"W={i['winners_avg']} L={i['losers_avg']}"
        )
    return "\n".join(lines)


def run_postmortem(conn: Any, *, now: int | None = None, force: bool = False) -> dict[str, Any]:
    """Full postmortem: RCA + importance + optional LLM + suggestions."""
    refresh_s56_config_from_env()
    now = int(now if now is not None else time.time())
    winners = top_winners(conn)
    losers = top_losers(conn)
    rca = compute_rca(winners, losers)
    importance = compute_feature_importance(winners, losers)
    llm_text, method = _call_llm_postmortem(
        conn, rca=rca, importance=importance, winners_n=len(winners), losers_n=len(losers),
    )
    closed_n = closed_trade_count(conn)

    cur = conn.execute(
        f"""
        INSERT INTO {_RUNS} (
          closed_count, winners_n, losers_n, rca_json, feature_importance_json,
          llm_text, llm_method, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            closed_n,
            len(winners),
            len(losers),
            json.dumps(rca, default=str),
            json.dumps(importance, default=str),
            llm_text,
            method,
            now,
        ),
    )
    run_id = None
    try:
        run_id = int(cur.lastrowid)
    except Exception:
        try:
            run_id = int(conn.execute(f"SELECT MAX(id) AS i FROM {_RUNS}").fetchone()["i"])
        except Exception:
            run_id = None

    suggestions = _suggest_from_stats(winners, losers, importance)
    for s in suggestions:
        conn.execute(
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
                s.get("source") or "deterministic_rca",
                now,
            ),
        )

    _ops_set(conn, "last_postmortem_closed_count", str(closed_n), now=now)
    _ops_set(conn, "last_postmortem_run_id", str(run_id or 0), now=now)

    return {
        "run_id": run_id,
        "closed_count": closed_n,
        "winners_n": len(winners),
        "losers_n": len(losers),
        "rca": rca,
        "feature_importance": importance,
        "llm_method": method,
        "llm_text": llm_text,
        "suggestions_created": len(suggestions),
        "forced": force,
    }


def maybe_run_postmortem(conn: Any, *, now: int | None = None) -> dict[str, Any] | None:
    """Trigger every S56_RCA_EVERY_N closed trades."""
    refresh_s56_config_from_env()
    closed_n = closed_trade_count(conn)
    try:
        last = int(_ops_get(conn, "last_postmortem_closed_count", "0"))
    except ValueError:
        last = 0
    if closed_n < S56_RCA_EVERY_N:
        return None
    if closed_n - last < S56_RCA_EVERY_N and last > 0:
        return None
    # Also require enough snapshots
    try:
        snap_n = int(conn.execute(f"SELECT COUNT(*) AS n FROM {_SNAP}").fetchone()["n"] or 0)
    except Exception:
        snap_n = 0
    if snap_n < max(50, S56_MIN_EVIDENCE * 2):
        return None
    return run_postmortem(conn, now=now, force=False)


def list_suggestions(
    conn: Any,
    *,
    status: str | None = STATUS_WAITING,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            f"""
            SELECT * FROM {_SUGGEST}
            WHERE status = ?
            ORDER BY id DESC LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM {_SUGGEST} ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row(r) for r in rows]


def approve_suggestion(conn: Any, suggestion_id: int, *, now: int | None = None) -> dict[str, Any]:
    """Human approval only — does NOT change trading code."""
    now = int(now if now is not None else time.time())
    row = conn.execute(
        f"SELECT * FROM {_SUGGEST} WHERE id = ?",
        (suggestion_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": "not_found"}
    task = (
        f"S56.{suggestion_id}\n"
        f"Implement ONLY this rule (nothing else):\n"
        f"{row['rule_text']}\n"
        f"Evidence trades={row['evidence_trades']} "
        f"Expected improvement={row['expected_improvement_pct']}% "
        f"Confidence={row['confidence_pct']}%"
    )
    conn.execute(
        f"""
        UPDATE {_SUGGEST}
        SET status = ?, decided_at = ?, cursor_task = ?
        WHERE id = ?
        """,
        (STATUS_APPROVED, now, task, suggestion_id),
    )
    return {"ok": True, "id": suggestion_id, "status": STATUS_APPROVED, "cursor_task": task}


def reject_suggestion(conn: Any, suggestion_id: int, *, now: int | None = None) -> dict[str, Any]:
    now = int(now if now is not None else time.time())
    cur = conn.execute(
        f"""
        UPDATE {_SUGGEST}
        SET status = ?, decided_at = ?
        WHERE id = ? AND status = ?
        """,
        (STATUS_REJECTED, now, suggestion_id, STATUS_WAITING),
    )
    return {"ok": (cur.rowcount or 0) > 0, "id": suggestion_id, "status": STATUS_REJECTED}


def format_suggestion(s: dict[str, Any]) -> str:
    return "\n".join([
        f"Suggestion #{s.get('id')}",
        f"Rule",
        f"  {s.get('rule_text')}",
        f"Evidence",
        f"  {s.get('evidence_trades')} trades",
        f"Expected Improvement",
        f"  +{float(s.get('expected_improvement_pct') or 0):.1f}%",
        f"Confidence",
        f"  {float(s.get('confidence_pct') or 0):.0f}%",
        f"Status",
        f"  {s.get('status')}",
        f"Source",
        f"  {s.get('source')}",
    ])


def format_postmortem_report(conn: Any) -> str:
    refresh_s56_config_from_env()
    winners = top_winners(conn, n=min(20, S56_TOP_N))
    losers = top_losers(conn, n=min(20, S56_TOP_N))
    w_n = len(top_winners(conn))
    l_n = len(top_losers(conn))
    waiting = list_suggestions(conn, status=STATUS_WAITING, limit=20)
    last = conn.execute(
        f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1",
    ).fetchone()
    lines = [
        "S56 Trade Post-Mortem",
        f"TOP winners stored/available: {w_n} (cap {S56_TOP_N})",
        f"TOP losers stored/available:  {l_n} (cap {S56_TOP_N})",
        f"RCA every N closed trades:    {S56_RCA_EVERY_N}",
        f"LLM enabled:                  {S56_LLM_ENABLED}",
        f"Closed trades (S42):          {closed_trade_count(conn)}",
        "",
        "Sample TOP winners (by PnL $):",
    ]
    for i, r in enumerate(winners[:10], 1):
        lines.append(
            f"  {i}. {r.get('symbol')} {r.get('direction')} "
            f"${float(r.get('pnl_usd') or 0):+.2f} exit={r.get('exit_reason')} "
            f"fund={r.get('funding')} ai={r.get('ai_score')}"
        )
    lines.append("Sample TOP losers:")
    for i, r in enumerate(losers[:10], 1):
        lines.append(
            f"  {i}. {r.get('symbol')} {r.get('direction')} "
            f"${float(r.get('pnl_usd') or 0):+.2f} exit={r.get('exit_reason')} "
            f"fund={r.get('funding')} ai={r.get('ai_score')}"
        )
    if last:
        lines.extend([
            "",
            f"Last run #{last['id']} method={last['llm_method']} "
            f"W={last['winners_n']} L={last['losers_n']}",
        ])
        try:
            imp = json.loads(last["feature_importance_json"] or "[]")
            lines.append("Feature importance:")
            for i in imp[:8]:
                lines.append(f"  {i.get('feature')}: {i.get('stars') or '·'} ({i.get('effect_size')})")
        except Exception:
            pass
        try:
            rca = json.loads(last["rca_json"] or "{}")
            lines.append("RCA (losers):")
            for f in (rca.get("what_unites_losers") or [])[:5]:
                lines.append(f"  - {f.get('statement')}")
        except Exception:
            pass
    lines.extend(["", f"Suggestions WAITING_APPROVAL: {len(waiting)}"])
    for s in waiting[:5]:
        lines.append("")
        lines.append(format_suggestion(s))
    lines.extend([
        "",
        "Human approval required. LLM/stats never change strategy.",
        "Approve: python -m bot.research.market_events approve-suggestion --suggestion-id N",
    ])
    return "\n".join(lines)


def format_s56_report_block(conn: Any) -> list[str]:
    refresh_s56_config_from_env()
    try:
        snap_n = int(conn.execute(f"SELECT COUNT(*) AS n FROM {_SNAP}").fetchone()["n"] or 0)
    except Exception:
        snap_n = 0
    waiting = list_suggestions(conn, status=STATUS_WAITING, limit=100)
    return [
        "",
        "S56 Post-Mortem",
        f"  snapshots={snap_n}  waiting_approval={len(waiting)}  "
        f"rca_every={S56_RCA_EVERY_N}  llm={S56_LLM_ENABLED}",
    ]


__all__ = [
    "STATUS_APPROVED",
    "STATUS_REJECTED",
    "STATUS_WAITING",
    "approve_suggestion",
    "compute_feature_importance",
    "compute_rca",
    "format_postmortem_report",
    "format_s56_report_block",
    "format_suggestion",
    "list_suggestions",
    "maybe_run_postmortem",
    "record_close_snapshot",
    "reject_suggestion",
    "refresh_s56_config_from_env",
    "run_postmortem",
    "top_losers",
    "top_winners",
]
