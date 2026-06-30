"""State-machine diagnostics for V4 Shadow."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def log_state_transition(phase: str, *, transition: bool = False, **fields: object) -> None:
    if transition:
        logger.info("↓")
    lines = [f"V4 STATE | {phase}"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, float):
            if key in {"probability"}:
                lines.append(f"{key}={value:.2f}")
            elif key in {"score"}:
                lines.append(f"{key}={value:.1f}")
            else:
                lines.append(f"{key}={value:.2f}")
        else:
            lines.append(f"{key}={value}")
    logger.info("\n".join(lines))


def log_blocked(reason: str, **fields: object) -> None:
    lines = ["V4 BLOCKED", "", f"reason:\n{reason}"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, float):
            if key in {"probability", "current", "need", "highest"}:
                lines.append(f"{key}={value:.2f}")
            elif key in {"score"}:
                lines.append(f"{key}={value:.1f}")
            else:
                lines.append(f"{key}={value}")
        else:
            lines.append(f"{key}={value}")
    logger.info("\n".join(lines))


def blocked_signature(reason: str, **fields: object) -> str:
    parts = [reason]
    for key in sorted(fields):
        value = fields[key]
        if value is not None:
            parts.append(f"{key}={value}")
    return "|".join(parts)
