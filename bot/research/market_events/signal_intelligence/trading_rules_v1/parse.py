"""Parse DNA setup labels into condition atoms; canonicalize display names."""

from __future__ import annotations

import re
from typing import Any

# Display aliases matching user-facing trading language.
_CANON = {
    "MACD+": "MACD>0",
    "MACD-": "MACD<0",
    "ATR%<0.6": "ATR<0.6",
    "Funding+": "Funding+",
    "Funding-": "Funding-",
    "OI+": "OI+",
    "OI-": "OI-",
}


def split_conditions(setup: str) -> list[str]:
    # Split on " + " only so labels like MACD+ / Funding+ stay intact.
    parts = [p.strip() for p in str(setup or "").split(" + ")]
    return [p for p in parts if p]


def canonicalize(cond: str) -> str:
    c = str(cond).strip()
    if c in _CANON:
        return _CANON[c]
    # ATR%<x → ATR<x
    m = re.match(r"^ATR%(<|<=|>|>=)(.+)$", c)
    if m:
        return f"ATR{m.group(1)}{m.group(2)}"
    return c


def is_coin_condition(cond: str) -> bool:
    return str(cond).startswith("Coin=")


def is_time_condition(cond: str) -> bool:
    c = str(cond)
    return c.startswith("Hour=") or c.startswith("Weekday=") or c.startswith("H")


def is_local_condition(cond: str) -> bool:
    return is_coin_condition(cond) or is_time_condition(cond)


def rule_from_setup(setup: str) -> dict[str, Any]:
    raw = split_conditions(setup)
    conds = [canonicalize(c) for c in raw]
    # preserve mapping raw→label for matching DNA masks via original setup string
    return {
        "setup": setup,
        "conditions": conds,
        "raw_conditions": raw,
        "n_conditions": len(conds),
    }


__all__ = [
    "canonicalize",
    "is_coin_condition",
    "is_local_condition",
    "is_time_condition",
    "rule_from_setup",
    "split_conditions",
]
