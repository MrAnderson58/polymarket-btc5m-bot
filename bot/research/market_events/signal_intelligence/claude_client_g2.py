"""Phase G.2 — native Anthropic Claude client (research only, no trading)."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Rough USD per 1M tokens for cost logging (research estimates).
_COST_PER_M_INPUT = {
    "claude-sonnet-5": 3.0,
    "claude-sonnet-4-20250514": 3.0,
    "default": 3.0,
}
_COST_PER_M_OUTPUT = {
    "claude-sonnet-5": 15.0,
    "claude-sonnet-4-20250514": 15.0,
    "default": 15.0,
}

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 529})


class ClaudeClientError(Exception):
    """Classified Claude API / parsing failure for fail-safe handling."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ClaudeUsageG2:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float


@dataclass
class ClaudeResponseG2:
    text: str
    usage: ClaudeUsageG2
    model: str
    raw: dict[str, Any] = field(default_factory=dict)


def _api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def default_model() -> str:
    return os.getenv("ME_G2_CLAUDE_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"


def timeout_sec() -> float:
    return float(os.getenv("ME_G2_TIMEOUT_SEC", "30"))


def max_retries() -> int:
    return int(os.getenv("ME_G2_MAX_RETRIES", "2"))


def is_claude_configured() -> bool:
    return bool(_api_key())


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate = _COST_PER_M_INPUT.get(model, _COST_PER_M_INPUT["default"])
    out_rate = _COST_PER_M_OUTPUT.get(model, _COST_PER_M_OUTPUT["default"])
    return round((input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate, 6)


def _extract_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _log_usage(model: str, usage: ClaudeUsageG2, *, label: str) -> None:
    logger.info(
        "g2 claude %s model=%s in_tokens=%d out_tokens=%d cost_usd=%.4f latency_ms=%.0f",
        label, model, usage.input_tokens, usage.output_tokens, usage.cost_usd, usage.latency_ms,
    )


def _post_messages(body: dict[str, Any]) -> dict[str, Any]:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=timeout_sec())
    if resp.status_code in _RETRYABLE_STATUS:
        raise requests.HTTPError(f"retryable {resp.status_code}", response=resp)
    resp.raise_for_status()
    return resp.json()


def call_claude_g2(
    *,
    system: str,
    user_content: list[dict[str, Any]] | str,
    model: str | None = None,
    max_tokens: int = 2048,
    label: str = "research",
) -> ClaudeResponseG2:
    """Call Anthropic Messages API with retries on transient errors."""
    model = model or default_model()
    if isinstance(user_content, str):
        content: list[dict[str, Any]] = [{"type": "text", "text": user_content}]
    else:
        content = user_content

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }

    last_exc: Exception | None = None
    t0 = time.perf_counter()
    data: dict[str, Any] = {}

    for attempt in range(max_retries() + 1):
        try:
            data = _post_messages(body)
            break
        except requests.Timeout as exc:
            last_exc = exc
            logger.warning("g2 claude attempt %d timeout: %s", attempt + 1, exc)
        except requests.ConnectionError as exc:
            last_exc = exc
            logger.warning("g2 claude attempt %d connection: %s", attempt + 1, exc)
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else 0
            if status not in _RETRYABLE_STATUS:
                if status == 429:
                    raise ClaudeClientError("http_429", f"rate limited: HTTP 429") from exc
                if status >= 500:
                    raise ClaudeClientError("http_5xx", f"server error: HTTP {status}") from exc
                raise ClaudeClientError("http_error", f"HTTP {status}: {exc}") from exc
            logger.warning("g2 claude attempt %d http %s", attempt + 1, status)
        if attempt < max_retries():
            time.sleep(min(2 ** attempt, 4))

    if not data:
        if isinstance(last_exc, requests.Timeout):
            raise ClaudeClientError("timeout", f"timeout after {timeout_sec()}s: {last_exc}")
        if isinstance(last_exc, requests.ConnectionError):
            raise ClaudeClientError("network", f"network error: {last_exc}")
        if isinstance(last_exc, requests.HTTPError):
            status = last_exc.response.status_code if last_exc.response is not None else 0
            if status == 429:
                raise ClaudeClientError("http_429", "rate limited after retries: HTTP 429")
            if status >= 500:
                raise ClaudeClientError("http_5xx", f"server error after retries: HTTP {status}")
        raise ClaudeClientError("unknown", f"claude call failed after retries: {last_exc}")

    usage_raw = data.get("usage") or {}
    in_tok = int(usage_raw.get("input_tokens") or 0)
    out_tok = int(usage_raw.get("output_tokens") or 0)
    latency = (time.perf_counter() - t0) * 1000.0
    cost = _estimate_cost(model, in_tok, out_tok)
    usage = ClaudeUsageG2(input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost, latency_ms=latency)
    _log_usage(model, usage, label=label)
    text = _extract_text(data)
    return ClaudeResponseG2(text=text, usage=usage, model=model, raw=data)


def call_claude_json_g2(
    *,
    system: str,
    prompt: str,
    model: str | None = None,
    label: str = "research",
) -> tuple[dict[str, Any], ClaudeResponseG2]:
    system_json = system + "\nOutput ONLY valid JSON. No markdown fences."
    resp = call_claude_g2(system=system_json, user_content=prompt, model=model, label=label)
    try:
        return json.loads(resp.text), resp
    except json.JSONDecodeError as exc:
        text = resp.text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1]), resp
            except json.JSONDecodeError:
                pass
        raise ClaudeClientError("invalid_json", f"invalid JSON response: {exc}") from exc


def _image_block_from_path(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    media_type = "image/jpeg"
    if p.suffix.lower() == ".png":
        media_type = "image/png"
    elif p.suffix.lower() == ".webp":
        media_type = "image/webp"
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def call_claude_vision_json_g2(
    *,
    system: str,
    prompt: str,
    image_path: str | None = None,
    image_base64: str | None = None,
    image_media_type: str = "image/jpeg",
    model: str | None = None,
    label: str = "visual",
) -> tuple[dict[str, Any], ClaudeResponseG2]:
    content: list[dict[str, Any]] = []
    if image_path:
        block = _image_block_from_path(image_path)
        if block:
            content.append(block)
    elif image_base64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": image_media_type, "data": image_base64},
        })
    content.append({"type": "text", "text": prompt})
    system_json = system + "\nOutput ONLY valid JSON. No markdown fences."
    resp = call_claude_g2(system=system_json, user_content=content, model=model, label=label)
    try:
        return json.loads(resp.text), resp
    except json.JSONDecodeError as exc:
        text = resp.text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1]), resp
            except json.JSONDecodeError:
                pass
        raise ClaudeClientError("invalid_json", f"invalid JSON response: {exc}") from exc
