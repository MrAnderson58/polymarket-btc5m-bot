"""AlertService — create / list / evaluate notification rules."""

from __future__ import annotations

from bot.terminal.alerts.models import AlertHit, AlertKind, AlertRule
from bot.terminal.alerts.rules import (
    ai_alert,
    evaluate_rules,
    parse_direction_condition,
    parse_score_condition,
    price_alert,
    signal_alert,
)
from bot.terminal.scanner.scanner import ScannerService, get_scanner_service
from bot.terminal.watchlist.models import normalize_symbol


class AlertService:
    """In-memory alert rules (per process). Scanner is the evaluation source."""

    def __init__(self, *, scanner: ScannerService | None = None) -> None:
        self._scanner = scanner or get_scanner_service()
        self._rules: dict[str, AlertRule] = {}

    def create(self, rule: AlertRule) -> AlertRule:
        self._rules[rule.rule_id] = rule
        return rule

    def create_signal(
        self,
        user_id: str | int,
        symbol: str,
        *,
        direction: str | None = None,
        score_gt: float | None = None,
    ) -> AlertRule:
        return self.create(
            signal_alert(str(user_id), symbol, direction=direction, score_gt=score_gt)
        )

    def create_ai(self, user_id: str | int, symbol: str, *, score_gt: float = 85.0) -> AlertRule:
        return self.create(ai_alert(str(user_id), symbol, ai_gt=score_gt))

    def create_price(
        self,
        user_id: str | int,
        symbol: str,
        *,
        op: str = ">",
        price: float,
    ) -> AlertRule:
        return self.create(price_alert(str(user_id), symbol, op=op, price=price))

    def get(self, rule_id: str) -> AlertRule | None:
        return self._rules.get(rule_id)

    def list(self, user_id: str | int | None = None) -> list[AlertRule]:
        rows = list(self._rules.values())
        if user_id is not None:
            uid = str(user_id)
            rows = [r for r in rows if r.user_id == uid]
        return sorted(rows, key=lambda r: (r.symbol, r.rule_id))

    def remove(self, rule_id: str) -> bool:
        return self._rules.pop(rule_id, None) is not None

    def clear(self, user_id: str | int | None = None) -> int:
        if user_id is None:
            n = len(self._rules)
            self._rules.clear()
            return n
        uid = str(user_id)
        doomed = [rid for rid, r in self._rules.items() if r.user_id == uid]
        for rid in doomed:
            del self._rules[rid]
        return len(doomed)

    def evaluate(self, user_id: str | int | None = None) -> list[AlertHit]:
        """Pull current scanner snapshot and evaluate matching rules."""
        rules = self.list(user_id)
        scans = self._scanner.scan_all()
        return evaluate_rules(rules, scans)

    def parse_and_create(self, user_id: str | int, parts: list[str]) -> AlertRule:
        """Parse CLI-ish fragments: BTC score>85 | NVDA LONG | BTC price>100000."""
        if not parts:
            raise ValueError("Usage: /alert add BTC score>85")
        symbol = normalize_symbol(parts[0])
        rest = parts[1:]
        kind_hint: AlertKind | None = None
        direction: str | None = None
        score_gt: float | None = None
        price_val: float | None = None
        price_op = ">"

        for token in rest:
            low = token.lower().replace(" ", "")
            if low in {"price", "signal", "ai"}:
                kind_hint = AlertKind(low)
                continue
            d = parse_direction_condition(token)
            if d is not None:
                direction = str(d.value)
                continue
            s = parse_score_condition(token)
            if s is not None:
                score_gt = float(s.value)
                continue
            if low.startswith("price") and any(op in low for op in (">=", "<=", ">", "<")):
                for op in (">=", "<=", ">", "<"):
                    if op in low:
                        try:
                            price_val = float(low.split(op, 1)[1])
                            price_op = op
                        except ValueError:
                            price_val = None
                        break
                continue
            # bare number after score context already handled; ignore junk

        if price_val is not None or kind_hint == AlertKind.PRICE:
            if price_val is None:
                raise ValueError("Price alert needs price>N")
            return self.create_price(user_id, symbol, op=price_op, price=price_val)
        if kind_hint == AlertKind.AI or (score_gt is not None and direction is None and kind_hint != AlertKind.SIGNAL):
            # score-only → AI alert (unified score)
            return self.create_ai(user_id, symbol, score_gt=score_gt if score_gt is not None else 85.0)
        return self.create_signal(user_id, symbol, direction=direction, score_gt=score_gt)


_default: AlertService | None = None


def get_alert_service(*, scanner: ScannerService | None = None) -> AlertService:
    global _default
    if scanner is not None:
        return AlertService(scanner=scanner)
    if _default is None:
        _default = AlertService()
    return _default


def reset_alert_service() -> None:
    global _default
    _default = None


__all__ = [
    "AlertService",
    "get_alert_service",
    "reset_alert_service",
]
