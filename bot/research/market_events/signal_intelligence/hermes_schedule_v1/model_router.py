"""Hermes model router — DeepSeek daily, Opus weekly (cost-controlled)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["daily", "weekly"]

# Hard caps — never send lake-sized payloads.
MAX_PACKAGE_CHARS_DAILY = 80_000  # ~20k tokens rough
MAX_PACKAGE_CHARS_WEEKLY = 120_000
MAX_INPUT_TOKENS_DAILY = 40_000
MAX_INPUT_TOKENS_WEEKLY = 60_000


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    mode: str  # live | offline | insufficient_data | cost_guard
    error: str | None = None


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def deepseek_configured() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


def opus_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def daily_model() -> str:
    return os.getenv("HERMES_DAILY_MODEL", "deepseek-chat").strip() or "deepseek-chat"


def weekly_model() -> str:
    return (
        os.getenv("HERMES_WEEKLY_MODEL", "claude-opus-4-20250514").strip()
        or "claude-opus-4-20250514"
    )


def cost_guard(package_text: str, *, role: Role) -> str | None:
    """Return stop reason if package too large / empty."""
    if not package_text.strip() or package_text.strip() == "{}":
        return "INSUFFICIENT_DATA"
    limit = MAX_PACKAGE_CHARS_DAILY if role == "daily" else MAX_PACKAGE_CHARS_WEEKLY
    if len(package_text) > limit:
        return (
            f"COST_GUARD: package chars={len(package_text)} > {limit}; "
            "ask Python to aggregate further"
        )
    tok_limit = MAX_INPUT_TOKENS_DAILY if role == "daily" else MAX_INPUT_TOKENS_WEEKLY
    est = estimate_tokens(package_text)
    if est > tok_limit:
        return f"COST_GUARD: est_tokens={est} > {tok_limit}"
    return None


def call_deepseek(
    *,
    system: str,
    user: str,
    max_tokens: int = 4096,
    label: str = "hermes_daily",
) -> LLMResult:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return LLMResult(
            text="",
            provider="deepseek",
            model=daily_model(),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            mode="offline",
            error="DEEPSEEK_API_KEY missing",
        )
    import requests

    model = daily_model()
    url = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions").strip()
    t0 = time.time()
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
        timeout=float(os.getenv("HERMES_DAILY_TIMEOUT_SEC", "90")),
    )
    resp.raise_for_status()
    data = resp.json()
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    usage = data.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or estimate_tokens(system + user))
    out_tok = int(usage.get("completion_tokens") or estimate_tokens(text))
    # rough DeepSeek pricing placeholder
    cost = (in_tok * 0.14 + out_tok * 0.28) / 1_000_000
    return LLMResult(
        text=text.strip(),
        provider="deepseek",
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=round(cost, 6),
        mode="live",
    )


def call_opus(
    *,
    system: str,
    user: str,
    max_tokens: int = 4096,
    label: str = "hermes_weekly",
) -> LLMResult:
    if not opus_configured():
        return LLMResult(
            text="",
            provider="anthropic",
            model=weekly_model(),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            mode="offline",
            error="ANTHROPIC_API_KEY missing",
        )
    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            call_claude_g2,
        )

        # Temporarily force weekly model via env for this call
        prev = os.environ.get("ME_G2_CLAUDE_MODEL")
        os.environ["ME_G2_CLAUDE_MODEL"] = weekly_model()
        try:
            resp = call_claude_g2(
                system=system,
                user_content=user,
                max_tokens=max_tokens,
                label=label,
            )
        finally:
            if prev is None:
                os.environ.pop("ME_G2_CLAUDE_MODEL", None)
            else:
                os.environ["ME_G2_CLAUDE_MODEL"] = prev
        return LLMResult(
            text=resp.text.strip(),
            provider="anthropic",
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cost_usd=resp.usage.cost_usd,
            mode="live",
        )
    except Exception as exc:
        return LLMResult(
            text="",
            provider="anthropic",
            model=weekly_model(),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            mode="offline",
            error=f"{type(exc).__name__}:{exc}",
        )


def route_llm(
    *,
    role: Role,
    system: str,
    user: str,
    package_text: str,
    offline: bool = False,
) -> LLMResult:
    guard = cost_guard(package_text, role=role)
    if guard == "INSUFFICIENT_DATA":
        return LLMResult(
            text="INSUFFICIENT_DATA",
            provider="none",
            model="none",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            mode="insufficient_data",
            error=guard,
        )
    if guard and guard.startswith("COST_GUARD"):
        return LLMResult(
            text=guard,
            provider="none",
            model="none",
            input_tokens=estimate_tokens(package_text),
            output_tokens=0,
            cost_usd=0.0,
            mode="cost_guard",
            error=guard,
        )
    if offline:
        return LLMResult(
            text="",
            provider="offline",
            model="offline",
            input_tokens=estimate_tokens(system + user),
            output_tokens=0,
            cost_usd=0.0,
            mode="offline",
            error="offline_forced",
        )
    if role == "daily":
        return call_deepseek(system=system, user=user)
    return call_opus(system=system, user=user)


def package_fingerprint(package: dict[str, Any]) -> str:
    blob = json.dumps(package, sort_keys=True, default=str)
    import hashlib

    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
