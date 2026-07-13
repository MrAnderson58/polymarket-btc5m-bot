"""Phase G.2 Task F — post-paper learning notes via Claude (hypotheses only)."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import load_recent_candles

_LEARNING_SYSTEM = """You are a research analyst reviewing completed paper trades.
Identify which signal features were useful vs misleading and suggest experiments.
You do NOT change trading rules or confidence automatically. Output JSON only."""

_LEARNING_SCHEMA = {
    "useful_signals": ["list — most helpful features"],
    "false_signals": ["list — misleading features"],
    "next_experiments": ["list — what to test next"],
    "summary_ru": "short research note in Russian",
}


def _price_path_after_entry(
    conn: Any,
    *,
    symbol: str,
    entry_ts: int,
    entry_price: float,
) -> list[dict[str, Any]]:
    bars = load_recent_candles(conn, symbol=symbol, timeframe="5m", limit=60)
    path: list[dict[str, Any]] = []
    for b in bars:
        if b.open_ts < entry_ts:
            continue
        if entry_price <= 0:
            continue
        path.append({
            "ts": b.open_ts,
            "close": b.close,
            "move_pct": round((b.close / entry_price - 1.0) * 100.0, 3),
        })
    return path[:20]


def record_paper_learning_g2(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    entry_ts: int,
    entry_price: float,
    net_return: float,
    exit_reason: str | None,
    duration_seconds: int | None,
) -> None:
    from bot.research.market_events.signal_intelligence.config import G2_ENABLED

    if not G2_ENABLED:
        return

    g2 = conn.execute(
        "SELECT response_json, g1_signal_type FROM market_events_ai_research_g2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    g1 = conn.execute(
        "SELECT signal_type, reversal_probability, mtf_windows_json FROM market_events_liquidity_trend_g1 WHERE event_id = ?",
        (event_id,),
    ).fetchone()

    payload = {
        "event_id": event_id,
        "symbol": symbol,
        "net_return_pct": net_return,
        "exit_reason": exit_reason,
        "duration_seconds": duration_seconds,
        "g1_signal_type": g1["signal_type"] if g1 else None,
        "g1_reversal_prob": float(g1["reversal_probability"]) if g1 else None,
        "prior_research": json.loads(g2["response_json"]) if g2 and g2["response_json"] else None,
        "price_path": _price_path_after_entry(conn, symbol=symbol, entry_ts=entry_ts, entry_price=entry_price),
    }

    prompt = json.dumps({
        "instruction": "Analyze this closed paper trade for research learning.",
        "required_schema": _LEARNING_SCHEMA,
        "trade": payload,
    }, ensure_ascii=False, default=str)

    result: dict[str, Any]
    in_tok = out_tok = 0
    cost = 0.0
    provider = "deterministic"
    model = ""

    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            call_claude_json_g2,
            default_model,
            is_claude_configured,
        )
        if is_claude_configured():
            parsed, resp = call_claude_json_g2(
                system=_LEARNING_SYSTEM, prompt=prompt, label="g2_learning",
            )
            result = {
                "useful_signals": [str(x) for x in parsed.get("useful_signals", [])][:6],
                "false_signals": [str(x) for x in parsed.get("false_signals", [])][:6],
                "next_experiments": [str(x) for x in parsed.get("next_experiments", [])][:6],
                "summary_ru": str(parsed.get("summary_ru") or ""),
            }
            in_tok, out_tok, cost = resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cost_usd
            provider, model = "anthropic", resp.model
        else:
            result = _learning_deterministic(payload, net_return)
    except Exception:
        result = _learning_deterministic(payload, net_return)

    now = int(time.time())
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_g2_learning_notes (
          event_id, symbol, useful_signals_json, false_signals_json,
          experiments_json, note_json, summary_ru, provider, model,
          input_tokens, output_tokens, cost_usd, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, symbol,
            json.dumps(result["useful_signals"], ensure_ascii=False),
            json.dumps(result["false_signals"], ensure_ascii=False),
            json.dumps(result["next_experiments"], ensure_ascii=False),
            json.dumps({**result, "trade": payload}, ensure_ascii=False),
            result["summary_ru"], provider, model,
            in_tok, out_tok, cost, now,
        ),
    )


def _learning_deterministic(payload: dict[str, Any], net_return: float) -> dict[str, Any]:
    useful: list[str] = []
    false_s: list[str] = []
    if payload.get("g1_signal_type"):
        useful.append(f"G1 {payload['g1_signal_type']}")
    if payload.get("g1_reversal_prob") and float(payload["g1_reversal_prob"]) >= 0.65:
        useful.append("высокая G1 reversal probability")
    if net_return > 0:
        useful.append("откат подтвердился после входа")
    else:
        false_s.append("ожидаемый откат не реализовался")
    if len(payload.get("price_path") or []) < 2:
        false_s.append("мало данных по пути цены после входа")
    return {
        "useful_signals": useful[:4] or ["структура G1"],
        "false_signals": false_s[:4] or ["—"],
        "next_experiments": [
            "сравнить candles_to_reversal с G1 streak",
            "проверить funding+OI combo на большей выборке",
        ],
        "summary_ru": f"Paper {'win' if net_return > 0 else 'loss'} {net_return:+.2f}% — заметка для гипотез.",
    }
