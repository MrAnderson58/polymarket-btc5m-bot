"""Live approval gate — AI / Brain / Scientist / Review must agree."""

from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from bot.ai_agent.decision import apply_decision
from bot.config import is_live_trading_enabled
from bot.portfolio.portfolio import PortfolioManager
from bot.strategy_review.cache import load_strategy_review_cache


@dataclass(frozen=True)
class LiveApprovalResult:
    allowed: bool
    entry_price: float | None
    ai_decision: str
    ai_confidence_pct: float
    brain: str
    scientist: str
    review: str
    risk: str
    blockers: tuple[str, ...]
    summary: str

    @property
    def has_block(self) -> bool:
        return bool(self.blockers)


def _entry_from_caller() -> dict[str, Any] | None:
    frame = inspect.currentframe()
    try:
        while frame is not None:
            order = frame.f_locals.get("order")
            if order is not None and hasattr(order, "price"):
                return {
                    "entry_price": float(order.price),
                    "strategy_name": getattr(order, "strategy_name", ""),
                    "market_slug": getattr(order, "market_slug", ""),
                    "side": getattr(order, "side", "NO"),
                    "size_usdc": float(getattr(order, "size_usdc", 0) or 0),
                }
            frame = frame.f_back
    finally:
        del frame
    return None


def _ai_verdict(features: dict[str, Any]) -> tuple[str, float]:
    scored = apply_decision(features)
    decision = scored.get("decision", "SHADOW")
    score = float(scored.get("ai_score", 0))
    return decision, score


def _brain_verdict(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT explainability_json
        FROM brain_trade_context
        ORDER BY built_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return "ALLOW"
    hints = json.loads(row["explainability_json"] or "{}")
    if hints.get("hint_skip_if"):
        return "BLOCK"
    if hints.get("hint_allow_if"):
        return "ALLOW"
    return "ALLOW"


def _scientist_verdict(conn: sqlite3.Connection) -> str:
    from bot.scientist.experiments import load_experiments
    from bot.scientist.scheduler import _best_next_step

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM early_reversion_v2_trades WHERE status = 'closed'"
    ).fetchone()["n"]
    step = _best_next_step(load_experiments(conn, limit=200), int(total))
    return "PASS" if not step.get("blocked") else "BLOCK"


def _review_verdict() -> str:
    review = load_strategy_review_cache()
    verdict = review.get("final_verdict", {})
    if verdict.get("safety_blocked"):
        return "BLOCK"
    decision = str(verdict.get("decision", "KEEP CURRENT SETTINGS"))
    if decision.startswith("KEEP"):
        return "KEEP"
    return "BLOCK"


def evaluate_live_approval(
    conn: sqlite3.Connection,
    *,
    entry_context: dict[str, Any] | None = None,
) -> LiveApprovalResult:
    ctx = entry_context or _entry_from_caller() or {}
    entry_price = ctx.get("entry_price")
    features = {
        "entry_price": entry_price or 0.39,
        "side": ctx.get("side", "NO"),
        "spread": 0.01,
        "btc_move_30s": 0.0,
        "regime_label": "Range",
    }
    ai_decision, ai_score = _ai_verdict(features)
    brain = _brain_verdict(conn)
    scientist = _scientist_verdict(conn)
    review = _review_verdict()
    risk = PortfolioManager.load(conn).state.current_risk

    blockers: list[str] = []
    if ai_decision != "ALLOW":
        blockers.append(f"AI {ai_decision}")
    if brain == "BLOCK":
        blockers.append("Brain BLOCK")
    if scientist == "BLOCK":
        blockers.append("Scientist BLOCK")
    if review == "BLOCK":
        blockers.append(f"Review {review}")
    if risk == "HIGH":
        blockers.append("Risk HIGH")

    allowed = True
    if is_live_trading_enabled():
        allowed = not blockers

    summary_lines = [
        f"ENTRY {entry_price:.2f}" if entry_price is not None else "ENTRY n/a",
        f"confidence {ai_score:.0f}%",
        f"brain {brain}",
        f"scientist {scientist}",
        f"review {review}",
        f"risk {risk}",
    ]
    if blockers:
        summary_lines.append(f"BLOCK: {', '.join(blockers)}")

    return LiveApprovalResult(
        allowed=allowed,
        entry_price=entry_price,
        ai_decision=ai_decision,
        ai_confidence_pct=ai_score,
        brain=brain,
        scientist=scientist,
        review=review,
        risk=risk,
        blockers=tuple(blockers),
        summary="\n".join(summary_lines),
    )
