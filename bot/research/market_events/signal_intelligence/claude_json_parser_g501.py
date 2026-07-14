"""Phase G.5.0.1 — robust Claude JSON extraction and repair."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_ANALYSIS_RE = re.compile(r"<analysis>\s*([\s\S]*?)\s*</analysis>", re.IGNORECASE)


def _try_load(s: str) -> dict[str, Any] | None:
    s = s.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_balanced_json(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text[start:], start=start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def repair_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON repair for common Claude formatting issues."""
    blob = text.strip()
    if not blob:
        return None

    parsed = _try_load(blob)
    if parsed:
        return parsed

    candidate = _extract_balanced_json(blob)
    if candidate:
        parsed = _try_load(candidate)
        if parsed:
            return parsed

    fixes = blob
    fixes = fixes.replace("'", '"')
    fixes = re.sub(r",\s*}", "}", fixes)
    fixes = re.sub(r",\s*]", "]", fixes)
    fixes = re.sub(r"\bTrue\b", "true", fixes)
    fixes = re.sub(r"\bFalse\b", "false", fixes)
    fixes = re.sub(r"\bNone\b", "null", fixes)

    candidate = _extract_balanced_json(fixes) or fixes
    return _try_load(candidate)


def extract_json_from_claude_text(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse Claude output: bare JSON, fenced ```json, or <analysis> blocks."""
    if not text or not text.strip():
        return None, "empty response"

    stripped = text.strip()
    direct = _try_load(stripped)
    if direct:
        return direct, None

    for m in _FENCE_RE.finditer(stripped):
        parsed = _try_load(m.group(1))
        if parsed:
            return parsed, None
        repaired = repair_json(m.group(1))
        if repaired:
            return repaired, None

    for m in _ANALYSIS_RE.finditer(stripped):
        parsed = _try_load(m.group(1))
        if parsed:
            return parsed, None
        repaired = repair_json(m.group(1))
        if repaired:
            return repaired, None

    balanced = _extract_balanced_json(stripped)
    if balanced:
        parsed = _try_load(balanced)
        if parsed:
            return parsed, None
        repaired = repair_json(balanced)
        if repaired:
            return repaired, None

    repaired = repair_json(stripped)
    if repaired:
        return repaired, None

    return None, "no valid JSON object found"
