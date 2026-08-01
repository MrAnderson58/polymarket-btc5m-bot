"""Map production gate + brain fusion into comparable shadow actions."""

from __future__ import annotations

from typing import Any

_PASS_GATES = {
    "PASS",
    "ALLOWED",
    "OPEN",
    "OK",
    "ACCEPT",
    "ACCEPTED",
    "REGIME_EXPLORE",
    "COLD_START",
}


def _dir_to_action(direction: str | None) -> str:
    d = str(direction or "").upper()
    if d in ("LONG", "BUY", "UP"):
        return "BUY"
    if d in ("SHORT", "SELL", "DOWN"):
        return "SELL"
    return "HOLD"


def production_action_from_trade(trade: dict[str, Any]) -> str:
    """
    Production shadow action from gate + direction.

    PASS-like gates (or closed trades without explicit reject) → BUY/SELL.
    Reject / insufficient → SKIP.
    """
    gate = str(
        trade.get("gate_decision")
        or trade.get("gate")
        or trade.get("gate_result")
        or ""
    ).upper()
    status = str(trade.get("status") or "").upper()
    state = str(trade.get("candidate_state") or "").upper()

    if gate and any(x in gate for x in ("REJECT", "BLOCK", "FAIL", "DENY", "INSUFFICIENT", "SKIP")):
        return "SKIP"
    if gate in _PASS_GATES:
        return _dir_to_action(str(trade.get("direction")))
    if state in ("REJECTED", "INSUFFICIENT"):
        return "SKIP"
    if state in ("ACCEPTED", "PROVISIONAL"):
        return _dir_to_action(str(trade.get("direction")))
    # Closed/open paper trade with PnL implies production took the trade
    if status in ("CLOSED", "OPEN") and trade.get("pnl") is not None:
        return _dir_to_action(str(trade.get("direction")))
    if status in ("CLOSED", "OPEN") and not gate:
        return _dir_to_action(str(trade.get("direction")))
    return "SKIP"


def action_hit(action: str, pnl: float | None) -> bool | None:
    """Whether action was correct given realized pnl. None if unevaluable."""
    if pnl is None:
        return None
    a = str(action or "").upper()
    if a in ("BUY", "LONG"):
        return float(pnl) > 0
    if a in ("SELL", "SHORT"):
        return float(pnl) < 0
    if a in ("SKIP", "HOLD", "NO_TRADE"):
        return float(pnl) <= 0
    return None


def realized_ev_for_action(action: str, pnl: float | None) -> float:
    """Contribution of action to realized EV (0 if skipped)."""
    if pnl is None:
        return 0.0
    a = str(action or "").upper()
    if a in ("BUY", "LONG"):
        return float(pnl)
    if a in ("SELL", "SHORT"):
        return -float(pnl)
    return 0.0


def pick_winner(
    *,
    production_action: str,
    brain_action: str,
    pnl: float | None,
) -> str:
    if pnl is None:
        return "PENDING"
    p_ev = realized_ev_for_action(production_action, pnl)
    b_ev = realized_ev_for_action(brain_action, pnl)
    if abs(b_ev - p_ev) < 1e-9:
        p_hit = action_hit(production_action, pnl)
        b_hit = action_hit(brain_action, pnl)
        if b_hit and not p_hit:
            return "BRAIN"
        if p_hit and not b_hit:
            return "PRODUCTION"
        return "TIE"
    return "BRAIN" if b_ev > p_ev else "PRODUCTION"


__all__ = [
    "action_hit",
    "pick_winner",
    "production_action_from_trade",
    "realized_ev_for_action",
]
