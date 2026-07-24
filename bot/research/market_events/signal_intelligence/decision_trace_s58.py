"""S58 — Decision Trace (Explain Every Trade).

Write-only observability: reconstruct the full open/close decision chain.
Does NOT change strategy, gates, signals, or allow/deny outcomes.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from bot.research.market_events.db import execute_with_retry

logger = logging.getLogger(__name__)

_TABLE = "market_events_trade_decisions_s58"
_TRADES = "market_events_paper_trades_s42"

# Match S42 capital math for expected PnL $ display only (not used for trading).
_CAPITAL_USD = 100.0
_LEVERAGE = 20.0


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row(r: Any) -> dict[str, Any]:
    if r is None:
        return {}
    try:
        return dict(r)
    except Exception:
        return {}


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for x in values[period:]:
        ema = x * k + ema * (1.0 - k)
    return round(ema, 6)


def _candles_emas(conn: Any, symbol: str) -> dict[str, float | None]:
    """Best-effort EMA20/50/200 from historical candles — for trace only."""
    out: dict[str, float | None] = {"ema20": None, "ema50": None, "ema200": None}
    try:
        from bot.research.market_events.signal_intelligence.candles import load_recent_candles
        bars = load_recent_candles(
            conn, symbol=str(symbol).upper(), venue="binance_futures", timeframe="5m", limit=220,
        )
        closes = [float(b.close) for b in bars if b.close and b.close > 0]
        if not closes:
            return out
        out["ema20"] = _ema(closes, 20)
        out["ema50"] = _ema(closes, 50)
        out["ema200"] = _ema(closes, 200)
    except Exception:
        pass
    return out


def _expected_pnl_usd(expected_pnl_pct: float | None) -> float | None:
    if expected_pnl_pct is None:
        return None
    return round(_CAPITAL_USD * (float(expected_pnl_pct) / 100.0) * _LEVERAGE, 4)


def _portfolio_state(conn: Any, open_count: int | None = None) -> dict[str, Any]:
    state: dict[str, Any] = {"open_count": open_count, "status": "OK"}
    try:
        from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55
        s55.refresh_s55_config_from_env()
        max_open = int(getattr(s55, "S55_MAX_OPEN_TRADES", 25) or 25)
        state["max_open"] = max_open
        if open_count is None:
            n = conn.execute(
                f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status = 'OPEN'",
            ).fetchone()
            open_count = int(n["n"] or 0) if n else 0
            state["open_count"] = open_count
        if open_count is not None and open_count >= max_open:
            state["status"] = "AT_CAPACITY"
        else:
            state["status"] = "OK"
    except Exception:
        state["status"] = "OK"
    return state


def build_why_opened(
    *,
    direction: str,
    features: dict[str, Any],
    estimate: dict[str, Any],
    gate_decision: str,
    emas: dict[str, float | None] | None = None,
    portfolio: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Human-readable pass/fail reasons from already-available inputs."""
    reasons: list[dict[str, Any]] = []
    emas = emas or {}
    portfolio = portfolio or {}
    direction = str(direction or "").upper()
    e20, e50, e200 = emas.get("ema20"), emas.get("ema50"), emas.get("ema200")

    if e20 is not None and e50 is not None:
        if direction == "LONG" and e20 > e50:
            reasons.append({"tag": "EMA", "ok": True, "text": f"EMA20 ({e20}) > EMA50 ({e50})"})
        elif direction == "SHORT" and e20 < e50:
            reasons.append({"tag": "EMA", "ok": True, "text": f"EMA20 ({e20}) < EMA50 ({e50})"})
        elif direction == "LONG" and e20 <= e50:
            reasons.append({"tag": "EMA", "ok": False, "text": f"EMA20 ({e20}) ≤ EMA50 ({e50})"})
        elif direction == "SHORT" and e20 >= e50:
            reasons.append({"tag": "EMA", "ok": False, "text": f"EMA20 ({e20}) ≥ EMA50 ({e50})"})
        if e200 is not None:
            if direction == "LONG" and e50 is not None and e50 > e200:
                reasons.append({"tag": "EMA200", "ok": True, "text": f"EMA50 ({e50}) > EMA200 ({e200})"})
            elif direction == "SHORT" and e50 is not None and e50 < e200:
                reasons.append({"tag": "EMA200", "ok": True, "text": f"EMA50 ({e50}) < EMA200 ({e200})"})

    funding = _safe_float(features.get("funding"))
    if funding is not None:
        if direction == "LONG" and funding < 0:
            reasons.append({"tag": "Funding", "ok": True, "text": f"Funding отрицательный ({funding})"})
        elif direction == "SHORT" and funding > 0:
            reasons.append({"tag": "Funding", "ok": True, "text": f"Funding положительный ({funding})"})
        elif direction == "LONG" and funding >= 0:
            reasons.append({"tag": "Funding", "ok": False, "text": f"Funding не отрицательный ({funding})"})
        else:
            reasons.append({"tag": "Funding", "ok": False, "text": f"Funding не положительный ({funding})"})

    regime = features.get("market_regime")
    if regime:
        reasons.append({
            "tag": "Regime",
            "ok": True,
            "text": f"Regime = {regime}",
        })

    ai = _safe_float(features.get("ai_score"))
    if ai is not None:
        reasons.append({
            "tag": "AI",
            "ok": ai >= 0.5,
            "text": f"AI Score = {ai}",
        })

    exp = _safe_float(estimate.get("expected_pnl_pct"))
    if exp is not None:
        reasons.append({
            "tag": "ExpectedPnL",
            "ok": exp >= 0,
            "text": f"ExpectedPnL = {exp:+.4f}%",
        })

    gate_ok = str(gate_decision or "").upper() in (
        "ALLOWED", "INSUFFICIENT_HISTORY", "DISABLED", "REGIME_PASS", "REGIME_COLD",
    )
    # Opened trades always passed overall; gate_decision is the S55 code.
    passed_codes = {"ALLOWED", "INSUFFICIENT_HISTORY", "DISABLED"}
    reasons.append({
        "tag": "Gate",
        "ok": str(gate_decision or "").upper() in passed_codes or gate_ok,
        "text": f"Gate = {gate_decision or 'PASS'}",
    })

    reasons.append({
        "tag": "Portfolio",
        "ok": str(portfolio.get("status") or "OK") == "OK",
        "text": f"Portfolio = {portfolio.get('status') or 'OK'}"
        + (f" (open={portfolio.get('open_count')})" if portfolio.get("open_count") is not None else ""),
    })
    return reasons


def build_rejected_alternatives(
    *,
    direction: str,
    reasons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Opposite side as rejected alternative (trace narrative only)."""
    direction = str(direction or "").upper()
    alt = "SHORT" if direction == "LONG" else "LONG"
    fail_tags = [r["tag"] for r in reasons if r.get("ok") is False]
    # Prefer a primary filter name for the opposite side story.
    primary = fail_tags[0] if fail_tags else "Trend filter"
    if not fail_tags:
        # If all checks passed for chosen side, opposite is rejected by trend/regime alignment.
        for r in reasons:
            if r.get("tag") in ("EMA", "Regime", "Funding") and r.get("ok"):
                primary = r["tag"] if r["tag"] != "EMA" else "Trend filter"
                break
        else:
            primary = "Trend filter"
    return [{"direction": alt, "reason": primary}]


def record_decision_on_open(
    conn: Any,
    *,
    paper_trade_id: int,
    s40_signal_type: str,
    s40_signal_id: int,
    features: dict[str, Any],
    estimate: dict[str, Any],
    gate_decision: str,
    entry_price: float | None = None,
    opened_at: int | None = None,
    open_count: int | None = None,
    candidate_rank: int | None = None,
    now: int | None = None,
    emas: dict[str, float | None] | None = None,
) -> bool:
    """Persist one decision row for an opened paper trade. Never raises into trading."""
    now = int(now if now is not None else time.time())
    opened_at = int(opened_at if opened_at is not None else now)
    try:
        symbol = str(features.get("symbol") or "")
        direction = str(features.get("direction") or "")
        emas_map = emas if emas is not None else _candles_emas(conn, symbol)
        portfolio = _portfolio_state(conn, open_count=open_count)
        exp_pct = _safe_float(estimate.get("expected_pnl_pct"))
        why = build_why_opened(
            direction=direction,
            features=features,
            estimate=estimate,
            gate_decision=gate_decision,
            emas=emas_map,
            portfolio=portfolio,
        )
        # S66: stamp a compact entry_reason from why_opened tags at open.
        entry_tags: list[str] = []
        for item in why[:4]:
            if isinstance(item, dict):
                tag = item.get("tag") or item.get("reason")
                if tag:
                    entry_tags.append(str(tag))
        entry_reason = " + ".join(entry_tags)[:120] if entry_tags else str(gate_decision)
        features["entry_reason"] = entry_reason

        # Derive ema_trend from candle EMAs when available.
        e20 = _safe_float(emas_map.get("ema20"))
        e50 = _safe_float(emas_map.get("ema50"))
        if e20 is not None and e50 is not None:
            features["ema_trend"] = round(e20 - e50, 6)

        rejected = build_rejected_alternatives(direction=direction, reasons=why)
        feat_payload = {k: v for k, v in features.items() if k != "features_json"}
        feat_payload["entry_reason"] = entry_reason
        feat_payload["session"] = features.get("session")
        feat_payload["market_regime_version"] = features.get("market_regime_version")
        feat_payload["decision_confidence"] = features.get("decision_confidence") or features.get("ai_score")
        feat_payload["open_interest"] = features.get("open_interest")
        feat_payload["btc_dominance"] = features.get("btc_dominance")
        feat_payload["ema_trend"] = features.get("ema_trend")
        inputs = {
            "features": feat_payload,
            "estimate": {
                k: v for k, v in estimate.items()
                if k not in ("nearest_neighbours",)
            },
            "regime_meta": estimate.get("regime"),
            "emas": emas_map,
            "portfolio": portfolio,
            "entry_reason": entry_reason,
            "attribution_version": "s66_v1",
        }
        price = entry_price if entry_price is not None else _safe_float(features.get("entry"))
        execute_with_retry(
            conn,
            f"""
            INSERT INTO {_TABLE} (
              paper_trade_id, s40_signal_type, s40_signal_id, opened_at,
              symbol, direction, entry_price,
              market_regime, btc_return, ema20, ema50, ema200,
              atr, rsi, volume, funding, fear_greed,
              macro_score, news_score, ai_score,
              expected_pnl_pct, expected_pnl_usd, candidate_rank,
              gate_result, gate_reason, portfolio_state,
              why_opened_json, rejected_alternatives_json, inputs_json,
              created_at, updated_at
            ) VALUES (
              ?, ?, ?, ?,
              ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?,
              ?, ?, ?,
              ?, ?, ?,
              ?, ?, ?,
              ?, ?
            )
            ON CONFLICT(paper_trade_id) DO UPDATE SET
              gate_result=excluded.gate_result,
              gate_reason=excluded.gate_reason,
              why_opened_json=excluded.why_opened_json,
              inputs_json=excluded.inputs_json,
              updated_at=excluded.updated_at
            """,
            (
                int(paper_trade_id),
                str(s40_signal_type),
                int(s40_signal_id),
                opened_at,
                symbol,
                direction,
                price,
                features.get("market_regime"),
                _safe_float(features.get("regime_btc_return_pct")),
                emas_map.get("ema20"),
                emas_map.get("ema50"),
                emas_map.get("ema200"),
                _safe_float(features.get("atr")),
                _safe_float(features.get("rsi")),
                _safe_float(features.get("volume")),
                _safe_float(features.get("funding")),
                _safe_float(features.get("fear_greed")),
                _safe_float(features.get("macro_score")),
                _safe_float(features.get("news_score")),
                _safe_float(features.get("ai_score")),
                exp_pct,
                _expected_pnl_usd(exp_pct),
                candidate_rank,
                "PASS" if str(gate_decision).upper() in (
                    "ALLOWED", "INSUFFICIENT_HISTORY", "DISABLED",
                ) else str(gate_decision),
                str(gate_decision),
                json.dumps(portfolio, default=str),
                json.dumps(why, default=str),
                json.dumps(rejected, default=str),
                json.dumps(inputs, default=str),
                now,
                now,
            ),
        )
        return True
    except Exception as exc:
        logger.warning("s58 record_decision_on_open failed: %s", exc)
        return False


def finalize_decision_on_close(
    conn: Any,
    *,
    paper_trade_id: int,
    exit_reason: str | None,
    duration_sec: int | None,
    max_profit_pct: float | None,
    max_drawdown_pct: float | None,
    final_pnl_usd: float | None,
    final_pnl_pct: float | None = None,
    closed_at: int | None = None,
    now: int | None = None,
) -> bool:
    """Attach close outcome to an existing decision row."""
    now = int(now if now is not None else time.time())
    closed_at = int(closed_at if closed_at is not None else now)
    try:
        existing = conn.execute(
            f"SELECT id FROM {_TABLE} WHERE paper_trade_id = ?",
            (int(paper_trade_id),),
        ).fetchone()
        if not existing:
            # Soft stub so explain still works for pre-S58 opens closed later.
            execute_with_retry(
                conn,
                f"""
                INSERT INTO {_TABLE} (
                  paper_trade_id, opened_at, symbol, direction,
                  exit_reason, duration_sec, max_profit_pct, max_drawdown_pct,
                  final_pnl_usd, final_pnl_pct, closed_at, created_at, updated_at
                ) VALUES (?, ?, '?', '?', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(paper_trade_id), closed_at,
                    exit_reason, duration_sec, max_profit_pct, max_drawdown_pct,
                    final_pnl_usd, final_pnl_pct, closed_at, now, now,
                ),
            )
            # Enrich from trade row if present
            try:
                t = conn.execute(
                    f"SELECT * FROM {_TRADES} WHERE id = ?",
                    (int(paper_trade_id),),
                ).fetchone()
                if t:
                    execute_with_retry(
                        conn,
                        f"""
                        UPDATE {_TABLE} SET
                          symbol = ?, direction = ?, entry_price = ?,
                          opened_at = COALESCE(?, opened_at),
                          updated_at = ?
                        WHERE paper_trade_id = ?
                        """,
                        (
                            str(t["symbol"]),
                            str(t["direction"]),
                            _safe_float(t["entry"]),
                            int(t["created_at"] or closed_at),
                            now,
                            int(paper_trade_id),
                        ),
                    )
            except Exception:
                pass
            return True

        execute_with_retry(
            conn,
            f"""
            UPDATE {_TABLE} SET
              exit_reason = ?, duration_sec = ?,
              max_profit_pct = ?, max_drawdown_pct = ?,
              final_pnl_usd = ?, final_pnl_pct = ?,
              closed_at = ?, updated_at = ?
            WHERE paper_trade_id = ?
            """,
            (
                exit_reason,
                duration_sec,
                max_profit_pct,
                max_drawdown_pct,
                final_pnl_usd,
                final_pnl_pct,
                closed_at,
                now,
                int(paper_trade_id),
            ),
        )
        return True
    except Exception as exc:
        logger.warning("s58 finalize_decision_on_close failed: %s", exc)
        return False


def get_decision(conn: Any, trade_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {_TABLE} WHERE paper_trade_id = ?",
        (int(trade_id),),
    ).fetchone()
    if not row:
        # Fallback: try by decision id
        row = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE id = ?",
            (int(trade_id),),
        ).fetchone()
    if not row:
        return None
    d = _row(row)
    for key in ("why_opened_json", "rejected_alternatives_json", "inputs_json", "portfolio_state"):
        raw = d.get(key)
        if isinstance(raw, str) and raw:
            try:
                d[key.replace("_json", "") if key.endswith("_json") else key] = json.loads(raw)
            except Exception:
                pass
    if "why_opened_json" in d:
        try:
            d["why_opened"] = json.loads(d["why_opened_json"]) if isinstance(d["why_opened_json"], str) else d.get("why_opened")
        except Exception:
            d["why_opened"] = []
    if "rejected_alternatives_json" in d:
        try:
            d["rejected_alternatives"] = (
                json.loads(d["rejected_alternatives_json"])
                if isinstance(d["rejected_alternatives_json"], str)
                else d.get("rejected_alternatives")
            )
        except Exception:
            d["rejected_alternatives"] = []
    return d


def format_explain_trade(conn: Any, trade_id: int) -> str:
    d = get_decision(conn, trade_id)
    if not d:
        # Last resort: paper trade exists but no S58 row
        t = conn.execute(f"SELECT * FROM {_TRADES} WHERE id = ?", (int(trade_id),)).fetchone()
        if not t:
            return f"No trade / decision found for trade-id={trade_id}"
        t = _row(t)
        opened = datetime.fromtimestamp(int(t.get("created_at") or 0)).strftime("%Y-%m-%d %H:%M")
        return "\n".join([
            "========== TRADE =========",
            "",
            f"{t.get('direction')} {t.get('symbol')}",
            "",
            "Opened:",
            opened,
            "",
            "Why opened:",
            "(no S58 decision row — trade opened before Decision Trace)",
            "",
            "Result:",
            str(t.get("exit_reason") or t.get("status") or "—"),
            "",
            "PnL:",
            f"{float(t.get('pnl_usd') or 0):+.0f}$" if t.get("pnl_usd") is not None else "—",
        ])

    opened_ts = int(d.get("opened_at") or 0)
    opened = datetime.fromtimestamp(opened_ts).strftime("%Y-%m-%d %H:%M") if opened_ts else "—"
    why = d.get("why_opened") or []
    if isinstance(why, str):
        try:
            why = json.loads(why)
        except Exception:
            why = []
    rejected = d.get("rejected_alternatives") or []
    if isinstance(rejected, str):
        try:
            rejected = json.loads(rejected)
        except Exception:
            rejected = []

    lines = [
        "========== TRADE =========",
        "",
        f"{d.get('direction')} {d.get('symbol')}",
        "",
        "Opened:",
        opened,
        "",
        "Why opened:",
    ]
    if why:
        for r in why:
            mark = "✓" if r.get("ok") else "·"
            lines.append(f"{mark} {r.get('tag')}")
            if r.get("text"):
                lines.append(f"  {r.get('text')}")
    else:
        lines.append("(no reasons stored)")

    lines.extend(["", "Rejected alternatives:"])
    if rejected:
        for alt in rejected:
            lines.append(str(alt.get("direction") or "?"))
            lines.append("↓")
            lines.append("Reason:")
            lines.append(str(alt.get("reason") or "—"))
    else:
        lines.append("—")

    exp_usd = _safe_float(d.get("expected_pnl_usd"))
    final = _safe_float(d.get("final_pnl_usd"))
    lines.extend([
        "",
        "Result:",
        str(d.get("exit_reason") or ("OPEN" if not d.get("closed_at") else "—")),
        "",
        "PnL:",
        f"{final:+.0f}$" if final is not None else "—",
        "",
        "Expected:",
        f"{exp_usd:+.0f}$" if exp_usd is not None else "—",
    ])
    if final is not None and exp_usd is not None:
        lines.extend(["", "Difference:", f"{final - exp_usd:+.0f}$"])

    if d.get("duration_sec") is not None:
        lines.extend(["", "Duration:", f"{int(d['duration_sec'])}s"])
    if d.get("max_profit_pct") is not None:
        lines.extend(["", "Max profit (MFE %):", f"{float(d['max_profit_pct']):+.2f}"])
    if d.get("max_drawdown_pct") is not None:
        lines.extend(["", "Max drawdown (MAE %):", f"{float(d['max_drawdown_pct']):+.2f}"])

    lines.extend([
        "",
        f"trade-id={d.get('paper_trade_id')}  gate={d.get('gate_reason')}  "
        f"regime={d.get('market_regime') or '—'}",
    ])
    return "\n".join(lines)


def compute_decision_report(conn: Any, *, top_n: int = 15) -> dict[str, Any]:
    """TOP reason tags among most profitable / most losing closed decisions."""
    rows = conn.execute(
        f"""
        SELECT * FROM {_TABLE}
        WHERE final_pnl_usd IS NOT NULL AND why_opened_json IS NOT NULL
        ORDER BY final_pnl_usd DESC
        """,
    ).fetchall()
    decisions = [_row(r) for r in rows]
    winners = [d for d in decisions if float(d.get("final_pnl_usd") or 0) > 0]
    losers = [d for d in decisions if float(d.get("final_pnl_usd") or 0) < 0]

    def _reason_counts(items: list[dict[str, Any]], *, only_ok: bool | None = True) -> list[dict[str, Any]]:
        counter: Counter[str] = Counter()
        pnl_by: dict[str, float] = defaultdict(float)
        n_by: dict[str, int] = defaultdict(int)
        for d in items:
            try:
                why = json.loads(d.get("why_opened_json") or "[]")
            except Exception:
                why = []
            pnl = float(d.get("final_pnl_usd") or 0)
            seen: set[str] = set()
            for r in why:
                if only_ok is True and not r.get("ok"):
                    continue
                if only_ok is False and r.get("ok"):
                    continue
                tag = str(r.get("tag") or "?")
                if tag in seen:
                    continue
                seen.add(tag)
                counter[tag] += 1
                pnl_by[tag] += pnl
                n_by[tag] += 1
        out = []
        for tag, n in counter.most_common(top_n):
            out.append({
                "reason": tag,
                "n": n,
                "pnl": round(pnl_by[tag], 2),
                "avg_pnl": round(pnl_by[tag] / max(1, n_by[tag]), 2),
            })
        return out

    top_win_trades = sorted(winners, key=lambda x: float(x.get("final_pnl_usd") or 0), reverse=True)[:top_n]
    top_loss_trades = sorted(losers, key=lambda x: float(x.get("final_pnl_usd") or 0))[:top_n]

    def _brief(d: dict[str, Any]) -> dict[str, Any]:
        try:
            why = json.loads(d.get("why_opened_json") or "[]")
        except Exception:
            why = []
        tags = [str(r.get("tag")) for r in why if r.get("ok")]
        return {
            "trade_id": d.get("paper_trade_id"),
            "symbol": d.get("symbol"),
            "direction": d.get("direction"),
            "pnl": d.get("final_pnl_usd"),
            "exit": d.get("exit_reason"),
            "reasons": tags,
            "regime": d.get("market_regime"),
        }

    return {
        "n_decisions": len(decisions),
        "n_winners": len(winners),
        "n_losers": len(losers),
        "top_reasons_winners": _reason_counts(winners, only_ok=True),
        "top_reasons_losers": _reason_counts(losers, only_ok=True),
        "top_fail_reasons_losers": _reason_counts(losers, only_ok=False),
        "most_profitable_trades": [_brief(d) for d in top_win_trades],
        "most_losing_trades": [_brief(d) for d in top_loss_trades],
    }


def format_decision_report(conn: Any, *, top_n: int = 15) -> str:
    rep = compute_decision_report(conn, top_n=top_n)
    lines = [
        "S58 Decision Report",
        f"  decisions={rep['n_decisions']}  winners={rep['n_winners']}  losers={rep['n_losers']}",
        "",
        "TOP reasons on profitable trades",
    ]
    for r in rep["top_reasons_winners"] or []:
        lines.append(
            f"  {r['reason']:<14} n={r['n']:<4} PnL=${r['pnl']:+.2f}  avg=${r['avg_pnl']:+.2f}"
        )
    if not rep["top_reasons_winners"]:
        lines.append("  (none)")

    lines.extend(["", "TOP reasons on losing trades"])
    for r in rep["top_reasons_losers"] or []:
        lines.append(
            f"  {r['reason']:<14} n={r['n']:<4} PnL=${r['pnl']:+.2f}  avg=${r['avg_pnl']:+.2f}"
        )
    if not rep["top_reasons_losers"]:
        lines.append("  (none)")

    lines.extend(["", "Most profitable trades"])
    for t in rep["most_profitable_trades"] or []:
        lines.append(
            f"  #{t['trade_id']} {t['direction']} {t['symbol']} "
            f"${float(t['pnl'] or 0):+.2f} exit={t['exit']} "
            f"reasons={','.join(t.get('reasons') or [])}"
        )
    if not rep["most_profitable_trades"]:
        lines.append("  (none)")

    lines.extend(["", "Most losing trades"])
    for t in rep["most_losing_trades"] or []:
        lines.append(
            f"  #{t['trade_id']} {t['direction']} {t['symbol']} "
            f"${float(t['pnl'] or 0):+.2f} exit={t['exit']} "
            f"reasons={','.join(t.get('reasons') or [])}"
        )
    if not rep["most_losing_trades"]:
        lines.append("  (none)")

    lines.extend([
        "",
        "Explain: python -m bot.research.market_events explain-decision --trade-id N",
    ])
    return "\n".join(lines)


def doctor_s58_status(conn: Any) -> dict[str, Any]:
    try:
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {_TABLE}").fetchone()["n"]
        closed = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE closed_at IS NOT NULL",
        ).fetchone()["n"]
    except Exception as exc:
        return {"decisions": 0, "closed": 0, "ok": False, "error": str(exc)[:80]}
    return {"decisions": int(n or 0), "closed": int(closed or 0), "ok": True}


__all__ = [
    "build_rejected_alternatives",
    "build_why_opened",
    "compute_decision_report",
    "doctor_s58_status",
    "finalize_decision_on_close",
    "format_decision_report",
    "format_explain_trade",
    "get_decision",
    "record_decision_on_open",
]
