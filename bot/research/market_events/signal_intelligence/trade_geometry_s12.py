"""Phase S1.2 — Trade geometry validator (reject inverted TP/SL before send)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeometryCheckS12:
    ok: bool
    direction: str
    entry: float
    tp1: float
    tp2: float
    sl: float
    errors: list[str] = field(default_factory=list)

    def format_invalid_log(self) -> str:
        lines = [
            "SIGNAL INVALID",
            "",
            "Direction",
            self.direction,
            "",
            "Entry",
            f"{self.entry:g}",
            "",
            "TP1",
            f"{self.tp1:g}",
            "",
            "ERROR",
            self.errors[0] if self.errors else "invalid geometry",
        ]
        if len(self.errors) > 1:
            for err in self.errors[1:]:
                lines.extend(["", err])
        return "\n".join(lines)


def shock_direction_for_trade_side(direction: str) -> str:
    """Map trade side → compute_trade_plan_f71 shock_direction.

    compute_trade_plan_f71: Shock DOWN → LONG, Shock UP → SHORT.
    """
    side = (direction or "").upper()
    if side in ("LONG", "BUY", "UP"):
        return "DOWN"
    return "UP"


def validate_trade_geometry_s12(
    *,
    direction: str,
    entry: float | None,
    tp1: float | None,
    tp2: float | None,
    sl: float | None,
    tp3: float | None = None,
) -> GeometryCheckS12:
    """Validate TP/SL ordering for LONG / SHORT. Fail closed on missing levels."""
    side = (direction or "").upper()
    if side in ("BUY", "UP"):
        side = "LONG"
    elif side in ("SELL", "DOWN"):
        side = "SHORT"

    e = float(entry or 0)
    t1 = float(tp1 or 0)
    t2 = float(tp2 or 0)
    stop = float(sl or 0)
    errors: list[str] = []

    if e <= 0 or t1 <= 0 or t2 <= 0 or stop <= 0:
        errors.append("Missing Entry/TP/SL")
        return GeometryCheckS12(
            ok=False, direction=side or "?", entry=e, tp1=t1, tp2=t2, sl=stop, errors=errors,
        )

    if side == "LONG":
        if not (t1 > e):
            errors.append("TP below Entry")
        if not (t2 > t1):
            errors.append("TP2 below TP1")
        if not (stop < e):
            errors.append("SL above Entry")
        if tp3 is not None and float(tp3) > 0 and not (float(tp3) > t2):
            errors.append("TP3 below TP2")
    elif side == "SHORT":
        if not (t1 < e):
            errors.append("TP above Entry")
        if not (t2 < t1):
            errors.append("TP2 above TP1")
        if not (stop > e):
            errors.append("SL below Entry")
        if tp3 is not None and float(tp3) > 0 and not (float(tp3) < t2):
            errors.append("TP3 above TP2")
    else:
        errors.append(f"Unknown Direction {direction}")

    return GeometryCheckS12(
        ok=len(errors) == 0,
        direction=side,
        entry=e,
        tp1=t1,
        tp2=t2,
        sl=stop,
        errors=errors,
    )


def assert_sendable_geometry_s12(
    *,
    direction: str,
    entry: float | None,
    tp1: float | None,
    tp2: float | None,
    sl: float | None,
    tp3: float | None = None,
    context: str = "signal",
) -> GeometryCheckS12:
    """Validate and log SIGNAL INVALID when geometry fails. Caller must not send if not ok."""
    check = validate_trade_geometry_s12(
        direction=direction, entry=entry, tp1=tp1, tp2=tp2, sl=sl, tp3=tp3,
    )
    if not check.ok:
        logger.error("%s\n%s", context, check.format_invalid_log())
    return check
