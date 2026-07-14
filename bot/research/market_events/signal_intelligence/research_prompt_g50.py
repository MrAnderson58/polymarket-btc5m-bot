"""Phase G.5.0 — Quant Research Analyst prompt builder."""

from __future__ import annotations

import json
from typing import Any

MAX_PROMPT_TOKENS = 1200

SYSTEM_PROMPT_G50 = """You are a quantitative researcher. Never invent facts. Use ONLY supplied statistics.
Do not predict markets. Derive statistical observations only. Provide confidence (0-1) and sample_size.
Output ONLY valid JSON with keys:
top_factors, weak_factors, overestimated_factors, profitable_combinations, loss_combinations,
btc_vs_alt, symbol_specific, new_patterns, bad_patterns, tomorrow_hypotheses, research_improvements,
missing_info, confidence, sample_size
List items: {label, evidence, sample_size}. Combos include win_rate when known.
missing_info: what data would improve this research (e.g. 5m candles, funding history)."""

RESEARCH_QUESTIONS_G50 = (
    "Top useful factors?",
    "Useless factors?",
    "Overestimated factors?",
    "Profitable factor combos?",
    "Loss combos?",
    "BTC vs alts?",
    "Per-symbol thresholds?",
    "New patterns?",
    "Tomorrow hypotheses?",
    "Research process improvements?",
    "What information was MISSING to do better research?",
)


def estimate_prompt_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def build_compact_dataset_payload(conn: Any | None, dataset: dict[str, Any]) -> dict[str, Any]:
    records = dataset.get("records") or []
    stats = dataset.get("aggregate_stats") or {}

    def _compact_row(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "s": r.get("symbol"),
            "pnl": r.get("final_pnl"),
            "c": r.get("confidence"),
            "ms": r.get("market_score"),
            "f": r.get("funding"),
            "oi": r.get("oi"),
            "v": r.get("volume"),
            "acc": r.get("accepted"),
            "rej": (str(r.get("rejection_reason") or "")[:40] or None),
        }

    wins = sorted(
        [r for r in records if float(r.get("final_pnl") or 0) > 0],
        key=lambda x: float(x.get("final_pnl") or 0),
        reverse=True,
    )[:12]
    losses = sorted(
        [r for r in records if float(r.get("final_pnl") or 0) < 0],
        key=lambda x: float(x.get("final_pnl") or 0),
    )[:12]

    sym_wr: dict[str, dict[str, Any]] = {}
    for r in records:
        sym = str(r.get("symbol") or "")
        if not sym:
            continue
        bucket = sym_wr.setdefault(sym, {"n": 0, "w": 0})
        bucket["n"] += 1
        pnl = float(r.get("final_pnl") or 0)
        if pnl > 0 or (r.get("candidate_outcome") or {}).get("is_win"):
            bucket["w"] += 1
    for sym, b in sym_wr.items():
        b["wr"] = round(b["w"] / b["n"], 2) if b["n"] else 0

    payload: dict[str, Any] = {
        "n": stats.get("sample_size") or len(records),
        "wr": stats.get("win_rate"),
        "sym": sym_wr,
        "g4": dataset.get("g4_factor_stats") or [],
        "wins": [_compact_row(r) for r in wins],
        "losses": [_compact_row(r) for r in losses],
    }

    if conn is not None:
        try:
            payload["false_rejects"] = [
                dict(r) for r in conn.execute(
                    """
                    SELECT symbol, pnl_pct, blocking_filter FROM market_validation_analysis_g4
                    WHERE analysis_type = 'false_reject' ORDER BY pnl_pct DESC LIMIT 5
                    """,
                ).fetchall()
            ]
            payload["optimizer"] = [
                dict(r) for r in conn.execute(
                    """
                    SELECT symbol, min_confidence, min_market_score, win_rate, profit_factor
                    FROM market_validation_optimizer_g4
                    ORDER BY report_date DESC, profit_factor DESC LIMIT 5
                    """,
                ).fetchall()
            ]
        except Exception:
            pass

    return payload


def build_research_prompt_g50(dataset: dict[str, Any], *, conn: Any | None = None) -> str:
    """Compact prompt targeting <1200 tokens."""
    compact = build_compact_dataset_payload(conn, dataset)
    questions = "; ".join(f"{i + 1}) {q}" for i, q in enumerate(RESEARCH_QUESTIONS_G50))

    while True:
        body = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        prompt = f"Answer all 11 questions from this dataset. Questions: {questions}\nDATA:{body}"
        if estimate_prompt_tokens(prompt) <= MAX_PROMPT_TOKENS:
            break
        if compact.get("wins"):
            compact["wins"] = compact["wins"][: max(0, len(compact["wins"]) - 2)]
        elif compact.get("losses"):
            compact["losses"] = compact["losses"][: max(0, len(compact["losses"]) - 2)]
        elif len(compact.get("sym") or {}) > 5:
            sym = compact["sym"]
            compact["sym"] = dict(list(sym.items())[: max(5, len(sym) - 3)])
        else:
            break

    return prompt


def validate_research_json_g50(data: dict[str, Any]) -> dict[str, Any]:
    required_lists = (
        "top_factors", "weak_factors", "overestimated_factors",
        "profitable_combinations", "loss_combinations", "btc_vs_alt",
        "symbol_specific", "new_patterns", "bad_patterns",
        "tomorrow_hypotheses", "research_improvements", "missing_info",
    )
    out = dict(data)
    for key in required_lists:
        val = out.get(key)
        if not isinstance(val, list):
            out[key] = []
    out["confidence"] = float(out.get("confidence") or 0.5)
    out["sample_size"] = int(out.get("sample_size") or 0)
    if "research_score" in out:
        out["research_score"] = float(out["research_score"])
    return out
