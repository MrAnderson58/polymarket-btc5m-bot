"""Fixed exit policy evaluation for Phase D.1 (no optimization)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.signal_outcome_path import PathEvaluation, directional_return


@dataclass
class PolicyResult:
    policy_id: str
    return_pct: float | None
    exit_event: str
    notes: str = ""


def _exit_price_for_event(ev: PathEvaluation, event: str) -> float | None:
    if ev.entry_price is None:
        return None
    if event == "STOP" and ev.stop_price is not None:
        return ev.stop_price
    if event.startswith("TP"):
        idx = int(event[2:])
        for t in ev.targets:
            if t.ordinal == idx and t.validity == "VALID":
                return t.price
    if ev.markouts:
        return ev.markouts[-1].mark_price
    return ev.entry_price


def evaluate_exit_policy(ev: PathEvaluation, policy_id: str) -> PolicyResult:
    if ev.entry_status != "ENTERED" or ev.entry_price is None:
        return PolicyResult(policy_id, None, "NOT_ENTERED")

    valid_targets = [t for t in ev.targets if t.validity == "VALID"]
    entry = ev.entry_price
    direction = ev.direction

    if policy_id == "P1":
        terminal = ev.conservative_terminal or "TIMEOUT"
        if terminal.startswith("TP"):
            px = _exit_price_for_event(ev, terminal)
        elif terminal == "STOP":
            px = ev.stop_price
        else:
            px = ev.markouts[-1].mark_price if ev.markouts else entry
        if px is None:
            return PolicyResult(policy_id, None, "NO_EXIT_PRICE")
        return PolicyResult(
            policy_id,
            directional_return(direction, entry, px) * 100.0,
            terminal,
        )

    if policy_id == "P2":
        if not valid_targets:
            return PolicyResult(policy_id, None, "NO_TARGETS")
        tp1 = valid_targets[0].price
        ret_tp = directional_return(direction, entry, tp1) * 100.0 * 0.5
        if len(valid_targets) > 1 and ev.max_target_reached >= 2:
            tp2 = valid_targets[1].price
            ret_rest = directional_return(direction, entry, tp2) * 100.0 * 0.5
            return PolicyResult(policy_id, ret_tp + ret_rest, "TP2", notes="50% at TP1")
        if ev.conservative_terminal == "STOP" and ev.stop_price is not None:
            ret_sl = directional_return(direction, entry, ev.stop_price) * 100.0 * 0.5
            return PolicyResult(policy_id, ret_tp + ret_sl, "STOP", notes="50% at TP1")
        mark = ev.markouts[-1].mark_price if ev.markouts else entry
        return PolicyResult(
            policy_id,
            ret_tp + directional_return(direction, entry, mark) * 100.0 * 0.5,
            "TIMEOUT",
        )

    if policy_id == "P3":
        if not valid_targets:
            return PolicyResult(policy_id, None, "NO_TARGETS")
        weight = 1.0 / len(valid_targets)
        total = 0.0
        reached = ev.max_target_reached
        for i, t in enumerate(valid_targets):
            if i < reached:
                total += directional_return(direction, entry, t.price) * 100.0 * weight
            elif ev.conservative_terminal == "STOP" and ev.stop_price is not None:
                rem = 1.0 - i * weight
                total += directional_return(direction, entry, ev.stop_price) * 100.0 * rem
                break
        return PolicyResult(policy_id, total, ev.conservative_terminal or "TIMEOUT")

    horizon_map = {"P4": "1h", "P5": "4h", "P6": "24h"}
    if policy_id in horizon_map:
        h = horizon_map[policy_id]
        for m in ev.markouts:
            if m.horizon == h:
                return PolicyResult(
                    policy_id,
                    m.directional_return_pct,
                    f"HORIZON_{h}",
                )
        return PolicyResult(policy_id, None, "NO_MARKOUT")

    return PolicyResult(policy_id, None, "UNKNOWN_POLICY")


def evaluate_all_policies(ev: PathEvaluation) -> list[PolicyResult]:
    from bot.research.futures_agent.signal_outcome_constants import EXIT_POLICIES
    return [evaluate_exit_policy(ev, p) for p in EXIT_POLICIES]
