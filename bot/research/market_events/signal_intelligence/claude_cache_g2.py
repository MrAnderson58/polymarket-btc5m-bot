"""Phase G.2 — prompt context cache (identical context → no second API call)."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from bot.research.market_events.signal_intelligence.config import PROMPT_VERSION_G2

_CACHE_TABLE = "market_events_g2_prompt_cache"


def compute_g2_context_hash(compact_ctx: dict[str, Any]) -> str:
    payload = json.dumps(compact_ctx, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{PROMPT_VERSION_G2}:{payload}".encode("utf-8")).hexdigest()
    return digest


def load_prompt_cache(conn: Any, context_hash: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT response_json, provider, model, input_tokens, output_tokens, cost_usd
        FROM {_CACHE_TABLE} WHERE context_hash = ?
        """,
        (context_hash,),
    ).fetchone()
    if not row:
        return None
    now = int(time.time())
    conn.execute(
        f"""
        UPDATE {_CACHE_TABLE}
        SET hit_count = hit_count + 1, updated_at = ?
        WHERE context_hash = ?
        """,
        (now, context_hash),
    )
    try:
        parsed = json.loads(row["response_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    return {
        "parsed": parsed,
        "provider": str(row["provider"]),
        "model": str(row["model"]),
        "input_tokens": int(row["input_tokens"] or 0),
        "output_tokens": int(row["output_tokens"] or 0),
        "cost_usd": float(row["cost_usd"] or 0),
    }


def save_prompt_cache(
    conn: Any,
    *,
    context_hash: str,
    response: dict[str, Any],
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    now = int(time.time())
    conn.execute(
        f"""
        INSERT INTO {_CACHE_TABLE} (
          context_hash, response_json, provider, model,
          input_tokens, output_tokens, cost_usd, hit_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(context_hash) DO UPDATE SET
          response_json = excluded.response_json,
          provider = excluded.provider,
          model = excluded.model,
          input_tokens = excluded.input_tokens,
          output_tokens = excluded.output_tokens,
          cost_usd = excluded.cost_usd,
          updated_at = excluded.updated_at
        """,
        (
            context_hash,
            json.dumps(response, ensure_ascii=False),
            provider,
            model,
            input_tokens,
            output_tokens,
            cost_usd,
            now,
            now,
        ),
    )
