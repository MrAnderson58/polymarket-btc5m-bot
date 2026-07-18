"""Alert rule builders + evaluation against Scanner snapshots."""

from __future__ import annotations

from typing import Mapping

from bot.terminal.alerts.models import (
    AlertCondition,
    AlertHit,
    AlertKind,
    AlertRule,
    new_rule_id,
)
from bot.terminal.scanner.models import ScannerResult
from bot.terminal.watchlist.models import normalize_symbol


def price_alert(
    user_id: str,
    symbol: str,
    *,
    op: str = ">",
    price: float,
    rule_id: str | None = None,
) -> AlertRule:
    """Notify when last price crosses a threshold."""
    return AlertRule(
        rule_id=rule_id or new_rule_id(),
        user_id=str(user_id),
        kind=AlertKind.PRICE,
        symbol=normalize_symbol(symbol),
        conditions=(AlertCondition(field="price", op=op, value=float(price)),),
        message=f"Price {normalize_symbol(symbol)} {op}{price}",
    )


def signal_alert(
    user_id: str,
    symbol: str,
    *,
    direction: str | None = None,
    score_gt: float | None = None,
    rule_id: str | None = None,
) -> AlertRule:
    """Notify when a scan signal appears (direction / score)."""
    conditions: list[AlertCondition] = []
    if direction:
        conditions.append(
            AlertCondition(field="direction", op="==", value=str(direction).upper())
        )
    if score_gt is not None:
        conditions.append(AlertCondition(field="score", op=">", value=float(score_gt)))
    if not conditions:
        conditions.append(AlertCondition(field="score", op=">", value=0.0))
    sym = normalize_symbol(symbol)
    return AlertRule(
        rule_id=rule_id or new_rule_id(),
        user_id=str(user_id),
        kind=AlertKind.SIGNAL,
        symbol=sym,
        conditions=tuple(conditions),
        message=f"Signal {sym} " + " ".join(c.describe() for c in conditions),
    )


def ai_alert(
    user_id: str,
    symbol: str,
    *,
    ai_gt: float = 85.0,
    rule_id: str | None = None,
) -> AlertRule:
    """Notify when AI / unified score exceeds a threshold."""
    sym = normalize_symbol(symbol)
    return AlertRule(
        rule_id=rule_id or new_rule_id(),
        user_id=str(user_id),
        kind=AlertKind.AI,
        symbol=sym,
        conditions=(AlertCondition(field="score", op=">", value=float(ai_gt)),),
        message=f"AI {sym} score>{ai_gt}",
    )


def _cmp(left: float | str | None, op: str, right: float | str) -> bool:
    if left is None:
        return False
    if op in {"==", "="}:
        return str(left).upper() == str(right).upper()
    if op == "!=":
        return str(left).upper() != str(right).upper()
    try:
        lv = float(left)
        rv = float(right)
    except (TypeError, ValueError):
        return False
    if op == ">":
        return lv > rv
    if op == ">=":
        return lv >= rv
    if op == "<":
        return lv < rv
    if op == "<=":
        return lv <= rv
    return False


def _field_value(
    field: str,
    *,
    scan: ScannerResult | None,
    prices: Mapping[str, float] | None,
) -> float | str | None:
    key = field.strip().lower()
    if key == "price":
        if prices is None or scan is None:
            return None
        return prices.get(scan.symbol.upper()) or prices.get(normalize_symbol(scan.symbol))
    if scan is None:
        return None
    if key == "score":
        return float(scan.score)
    if key == "confidence":
        return scan.confidence
    if key == "direction":
        return (scan.direction or "").upper()
    if key == "ai":
        return scan.components.ai if scan.components.ai is not None else scan.score
    return None


def evaluate_rule(
    rule: AlertRule,
    *,
    scan: ScannerResult | None = None,
    prices: Mapping[str, float] | None = None,
) -> AlertHit | None:
    """Return AlertHit if all conditions match; else None."""
    if not rule.enabled:
        return None
    if scan is not None and normalize_symbol(scan.symbol) != normalize_symbol(rule.symbol):
        return None
    if scan is None and rule.kind != AlertKind.PRICE:
        return None

    for cond in rule.conditions:
        left = _field_value(cond.field, scan=scan, prices=prices)
        if not _cmp(left, cond.op, cond.value):
            return None

    reason = rule.message or rule.describe()
    return AlertHit(
        rule_id=rule.rule_id,
        user_id=rule.user_id,
        symbol=rule.symbol,
        kind=rule.kind,
        reason=reason,
        score=scan.score if scan else None,
        direction=scan.direction if scan else None,
        provider=scan.provider if scan else None,
        reasons=scan.reasons if scan else (),
        extra={"conditions": [c.describe() for c in rule.conditions]},
    )


def evaluate_rules(
    rules: list[AlertRule] | tuple[AlertRule, ...],
    scans: list[ScannerResult] | tuple[ScannerResult, ...],
    *,
    prices: Mapping[str, float] | None = None,
) -> list[AlertHit]:
    """Evaluate all enabled rules against a scan snapshot."""
    by_symbol: dict[str, ScannerResult] = {
        normalize_symbol(s.symbol): s for s in scans
    }
    hits: list[AlertHit] = []
    for rule in rules:
        if not rule.enabled:
            continue
        scan = by_symbol.get(normalize_symbol(rule.symbol))
        hit = evaluate_rule(rule, scan=scan, prices=prices)
        if hit is not None:
            hits.append(hit)
    return hits


def parse_score_condition(raw: str) -> AlertCondition | None:
    """Parse fragments like score>85, score>=90."""
    text = (raw or "").strip().lower().replace(" ", "")
    for op in (">=", "<=", ">", "<", "==", "="):
        if "score" in text and op in text:
            _, _, rhs = text.partition(op)
            if "score" in text.split(op)[0] or text.startswith("score"):
                try:
                    return AlertCondition(field="score", op=op if op != "=" else "==", value=float(rhs))
                except ValueError:
                    return None
    return None


def parse_direction_condition(raw: str) -> AlertCondition | None:
    text = (raw or "").strip().upper()
    if text in {"LONG", "SHORT", "FLAT"}:
        return AlertCondition(field="direction", op="==", value=text)
    return None


__all__ = [
    "ai_alert",
    "evaluate_rule",
    "evaluate_rules",
    "parse_direction_condition",
    "parse_score_condition",
    "price_alert",
    "signal_alert",
]
