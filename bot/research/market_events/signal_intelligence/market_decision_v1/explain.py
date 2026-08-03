"""Decision Explainability Engine V1 — human-readable WHY for each decision."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
)

MODULE_ORDER: tuple[str, ...] = (
    "replay",
    "fingerprint",
    "timeline",
    "dna",
    "rules",
    "edge",
    "causality",
    "brain",
)

MODULE_TITLE: dict[str, str] = {
    "replay": "Replay",
    "fingerprint": "Fingerprint",
    "timeline": "Timeline",
    "dna": "DNA",
    "rules": "Rules",
    "edge": "Edge",
    "causality": "Causality",
    "brain": "Brain",
}


def _score_01(module: str, s: dict[str, Any]) -> float | None:
    if s.get("similarity_pct") is not None:
        try:
            return round(min(0.99, max(0.0, float(s["similarity_pct"]) / 100.0)), 2)
        except Exception:
            return None
    if s.get("confidence") is not None:
        try:
            return round(min(0.99, max(0.0, float(s["confidence"]))), 2)
        except Exception:
            return None
    if module == "rules" and s.get("pass"):
        return 0.85
    if module == "dna" and s.get("pass"):
        return 0.80
    if module == "edge" and s.get("pass"):
        return 0.75
    return None


def _module_line(name: str, s: dict[str, Any]) -> tuple[str, str]:
    title = MODULE_TITLE.get(name, name.title())
    passed = bool(s.get("pass"))
    mark = "✔" if passed else "✖"
    score = _score_01(name, s)
    if name == "rules" and s.get("blocked") and s.get("rule"):
        return title, f"✖ {s.get('rule')}"
    if name == "rules" and passed and s.get("rule"):
        return title, f"✔ {s.get('rule')}"
    if name == "brain" and str(s.get("brain_decision") or "").upper() == "NO_TRADE":
        mark = "✖"
    if score is not None and mark == "✔":
        return title, f"{mark} {score:.2f}"
    if mark == "✔":
        return title, mark
    return title, mark


def decision_rank(result: dict[str, Any]) -> str:
    """A+/A/B/C/D research rank from confidence + historical edge."""
    if result.get("decision") != "TRADE":
        conf = float(result.get("confidence") or 0)
        if conf < 0.35:
            return "D"
        return "C"
    conf = float(result.get("confidence") or 0)
    wr = float(result.get("historical_wr") or 0)
    pf = result.get("historical_pf")
    pf_v = float(pf) if isinstance(pf, (int, float)) else (3.0 if pf == "inf" else 0.0)
    ev = float(result.get("historical_ev") or 0)
    supporting = int(result.get("supporting_modules") or 0)
    if conf >= 0.80 and wr >= 70 and pf_v >= 1.8 and supporting >= 4:
        return "A+"
    if conf >= 0.70 and wr >= 60 and (pf_v >= 1.4 or ev >= 1.0) and supporting >= 3:
        return "A"
    if conf >= 0.55 and wr >= 55:
        return "B"
    return "C"


def why_accepted_lines(result: dict[str, Any]) -> list[str]:
    scores = result.get("scores") or {}
    lines: list[str] = []
    for name in MODULE_ORDER:
        s = scores.get(name) or {}
        if not s.get("pass"):
            continue
        title = MODULE_TITLE.get(name, name)
        score = _score_01(name, s)
        if name == "rules" and s.get("rule"):
            lines.append(f"{title} matched ({s.get('rule')})")
        elif score is not None:
            lines.append(f"{title} support {score:.2f}")
        else:
            lines.append(f"{title} support")
        if s.get("reason") and name in ("fingerprint", "timeline", "replay"):
            # avoid duplicating generic pass reasons
            pass
    if result.get("historical_wr") is not None:
        lines.append(f"Historical WR {result.get('historical_wr')}%")
    if result.get("historical_pf") is not None:
        lines.append(f"Historical PF {result.get('historical_pf')}")
    if result.get("historical_ev") is not None:
        lines.append(f"Historical EV {result.get('historical_ev')}%")
    lines.append(f"Confidence {result.get('confidence_pct')}%")
    return lines


def explain_from_decision(result: dict[str, Any]) -> dict[str, Any]:
    """Attach rank + module board to an existing decide_one result."""
    rank = decision_rank(result)
    board: list[dict[str, Any]] = []
    scores = result.get("scores") or {}
    for name in MODULE_ORDER:
        s = scores.get(name) or {}
        title, line = _module_line(name, s)
        board.append({
            "module": name,
            "title": title,
            "pass": bool(s.get("pass")),
            "line": line,
            "score": _score_01(name, s),
            "reason": s.get("reason"),
        })
    out = {
        **result,
        "decision_rank": rank,
        "module_board": board,
        "why_accepted": why_accepted_lines(result) if result.get("decision") == "TRADE" else [],
        "explain_text": format_explain(result, rank=rank, board=board),
    }
    return out


def format_explain(
    result: dict[str, Any],
    *,
    rank: str | None = None,
    board: list[dict[str, Any]] | None = None,
) -> str:
    """Terminal card matching Decision Explainability V1 layout."""
    tid = result.get("trade_id")
    decision = result.get("decision")
    direction = result.get("direction")
    rank = rank or decision_rank(result)
    scores = result.get("scores") or {}
    if board is None:
        board = []
        for name in MODULE_ORDER:
            title, line = _module_line(name, scores.get(name) or {})
            board.append({"title": title, "line": line})

    lines: list[str] = [
        f"TRADE #{tid}",
        "",
        "Decision:",
    ]
    if decision == "TRADE" and direction:
        lines.append(f"TRADE {direction}")
    else:
        lines.append("NO TRADE")
    lines.extend(["", "======================", ""])

    for b in board:
        lines.append(str(b.get("title")))
        lines.append(str(b.get("line")))
        lines.append("")

    lines.append("----------------------")
    lines.append("")

    if decision == "TRADE":
        lines.extend([
            "Decision confidence",
            "",
            f"{result.get('confidence_pct')}%",
            "",
            "Historical WR",
            "",
            f"{result.get('historical_wr')}%",
            "",
            "Historical PF",
            "",
            f"{result.get('historical_pf')}",
            "",
            "Historical EV",
            "",
            f"{result.get('historical_ev')}%",
            "",
            "Decision Rank",
            "",
            f"{rank}",
            "",
            "======================",
            "",
            "Why accepted",
            "",
        ])
        for w in why_accepted_lines(result):
            lines.append(w)
    else:
        lines.extend([
            "Decision",
            "",
            "NO TRADE",
            "",
            "Reasons",
            "",
        ])
        for w in result.get("why") or result.get("reasons") or []:
            lines.append(str(w))
        if result.get("historical_wr") is not None and not any(
            "Historical WR" in str(x) for x in (result.get("why") or [])
        ):
            lines.append(f"Historical WR {result.get('historical_wr')}%")
        lines.append("")
        lines.append("Decision Rank")
        lines.append("")
        lines.append(f"{rank}")

    lines.extend(["", "Recommendation", "RESEARCH ONLY"])
    return "\n".join(lines)


def explain_trade(ctx: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    return explain_from_decision(decide_one(ctx, trade))


def find_trade(ctx: dict[str, Any], trade_id: int) -> dict[str, Any] | None:
    for t in ctx.get("trades") or []:
        if int(t.get("trade_id") or t.get("id") or 0) == int(trade_id):
            return t
    return None


__all__ = [
    "decision_rank",
    "explain_from_decision",
    "explain_trade",
    "find_trade",
    "format_explain",
]
