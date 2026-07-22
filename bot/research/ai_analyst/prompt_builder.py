"""S46 Prompt Builder — loads prompt files (no hardcoded analyst instructions)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.ai_analyst.config import PROMPTS_DIR


def load_prompt(name: str, *, prompts_dir: Path | None = None) -> str:
    path = (prompts_dir or PROMPTS_DIR) / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def build_messages(
    *,
    prompt_name: str,
    context: dict[str, Any],
    prompts_dir: Path | None = None,
) -> tuple[str, str]:
    """Return (system, user) messages. Analysis instructions live in prompt files."""
    base = load_prompt("system_base", prompts_dir=prompts_dir)
    try:
        rules = load_prompt("reasoning_rules", prompts_dir=prompts_dir)
        system = f"{base}\n\n{rules}"
    except FileNotFoundError:
        system = base
    task = load_prompt(prompt_name, prompts_dir=prompts_dir)
    intel = context.get("intelligence") or {}
    top_text = intel.get("top_events_text")
    compression = intel.get("compression") or {}
    stats = compression.get("stats") or {}
    preface_lines = [
        "Pre-compressed intelligence (S50): use ONLY intelligence.top_events "
        "(typically ≤8 items). Do not invent additional headlines.",
    ]
    if stats:
        preface_lines.append(
            f"Pipeline: raw={stats.get('raw_count')} → deduped={stats.get('deduped_count')} "
            f"→ clustered={stats.get('clustered_count')} → top={stats.get('top_count')}."
        )
    if top_text:
        preface_lines.extend(["", str(top_text)])
    preface = "\n".join(preface_lines)
    payload = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    user = (
        f"{task}\n\n"
        f"{preface}\n\n"
        "--- MARKET CONTEXT JSON (authoritative; do not invent beyond this) ---\n"
        f"{payload}\n"
        "--- END CONTEXT ---"
    )
    return system, user
