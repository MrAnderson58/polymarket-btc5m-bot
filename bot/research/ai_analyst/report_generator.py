"""S46 report / brief / JSON generators + orchestrator."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from bot.research.ai_analyst.config import (
    REPORTS_DIR,
    AgentProfile,
    LLMSettings,
    load_agent_profiles,
    load_llm_settings,
)
from bot.research.ai_analyst.context_builder import build_market_context, dump_context_json
from bot.research.ai_analyst.llm_client import LLMClient, LLMResponse, get_llm_client
from bot.research.ai_analyst.prompt_builder import build_messages
from bot.research.ai_analyst.reasoning_engine import (
    enrich_context_for_analysis,
    format_analysis_quality_block,
)

logger = logging.getLogger(__name__)

FLAG_TO_AGENT: dict[str, str] = {
    "morning": "s46_morning",
    "evening": "s46_evening",
    "btc": "s46_btc",
    "macro": "s46_macro",
    "sp500": "s46_sp500",
    "telegram": "s46_telegram",
    "x": "s46_x",
}

DEFAULT_RUN_AGENTS: tuple[str, ...] = (
    "s46_full",
    "s46_json",
    "s46_morning",
    "s46_evening",
    "s46_btc",
    "s46_macro",
    "s46_sp500",
    "s46_x",
    "s46_telegram",
)


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json|markdown|md)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def generate_artifact(
    *,
    profile: AgentProfile,
    context: dict[str, Any],
    client: LLMClient,
    reports_dir: Path | None = None,
) -> dict[str, Any]:
    system, user = build_messages(prompt_name=profile.prompt, context=context)
    resp: LLMResponse = client.complete(system=system, user=user)
    text = _strip_fences(resp.text)

    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / profile.output

    if profile.output.endswith(".json"):
        try:
            parsed = json.loads(text)
            out_path.write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            out_path.write_text(text + "\n", encoding="utf-8")
    else:
        if profile.prompt == "x_post":
            text = text.strip().strip('"')[:280]
        elif profile.prompt == "telegram_post":
            text = text.strip()[:1200]
        if profile.prompt == "full_report" and "## Analysis Quality" not in text:
            quality = (context.get("analysis_quality") or {})
            if quality:
                text = text.rstrip() + "\n\n" + format_analysis_quality_block(quality)
        out_path.write_text(text.rstrip() + "\n", encoding="utf-8")

    return {
        "agent_id": profile.agent_id,
        "prompt": profile.prompt,
        "path": str(out_path),
        "provider": resp.provider,
        "model": resp.model,
        "latency_ms": round(resp.latency_ms, 1),
        "error": resp.error,
        "chars": len(text),
    }


def run_ai_analyst(
    *,
    flags: list[str] | None = None,
    reports_dir: Path | None = None,
    write_context: bool = True,
    force_template: bool = False,
    now: int | None = None,
    live_enrich: bool = True,
    live_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build context once, then generate selected artifacts."""
    settings = load_llm_settings()
    if force_template:
        settings = LLMSettings(
            provider="template",
            model="template",
            timeout_sec=settings.timeout_sec,
            max_retries=0,
            max_tokens=settings.max_tokens,
            base_url=None,
            api_key=None,
        )
    client = get_llm_client(settings)
    profiles = load_agent_profiles()

    if flags:
        selected: list[str] = []
        for f in flags:
            key = f.lstrip("-").lower()
            if key in FLAG_TO_AGENT:
                selected.append(FLAG_TO_AGENT[key])
            elif key in profiles:
                selected.append(key)
        if not selected:
            selected = list(DEFAULT_RUN_AGENTS)
    else:
        selected = list(DEFAULT_RUN_AGENTS)

    context = build_market_context(
        now=now,
        live_enrich=live_enrich,
        live_payload=live_payload,
    )
    context = enrich_context_for_analysis(context)
    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    context_path = None
    if write_context:
        context_path = out_dir / "market_context.json"
        context_path.write_text(dump_context_json(context) + "\n", encoding="utf-8")

    artifacts: list[dict[str, Any]] = []
    for agent_id in selected:
        profile = profiles.get(agent_id)
        if not profile:
            logger.warning("unknown agent profile %s", agent_id)
            continue
        artifacts.append(
            generate_artifact(
                profile=profile,
                context=context,
                client=client,
                reports_dir=out_dir,
            )
        )

    return {
        "ok": True,
        "provider": settings.provider,
        "model": settings.model,
        "context_path": str(context_path) if context_path else None,
        "events": len((context.get("intelligence") or {}).get("top_events") or []),
        "artifacts": artifacts,
    }
