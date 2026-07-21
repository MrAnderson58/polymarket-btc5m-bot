"""S46 config + agent profiles (YAML + env overrides)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

CONFIG_PATH = BASE_DIR / "config" / "ai_analyst.yaml"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
REPORTS_DIR = BASE_DIR / "reports"


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    timeout_sec: float
    max_retries: int
    max_tokens: int
    base_url: str | None
    api_key: str | None


@dataclass(frozen=True)
class AgentProfile:
    """Future S47–S52 agents = new profile (same context, different prompt/output)."""

    agent_id: str
    prompt: str
    output: str


@dataclass(frozen=True)
class TelegramTerminalSettings:
    report_cache_minutes: int
    enable_progress_messages: bool
    default_report: str
    parse_mode: str


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal indented YAML subset (no dependency on PyYAML)."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            if val.startswith("#"):
                val = ""
            if "#" in val:
                val = val.split("#", 1)[0].strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            elif val.lower() in ("true", "false"):
                val = val.lower() == "true"
            else:
                try:
                    if "." in val:
                        val = float(val)
                    else:
                        val = int(val)
                except ValueError:
                    pass
            parent[key] = val
    return root


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or CONFIG_PATH
    if not cfg_path.is_file():
        return {
            "llm": {
                "provider": "template",
                "model": "template",
                "timeout_sec": 90,
                "max_retries": 1,
                "max_tokens": 4096,
            },
            "agents": {},
        }
    return _parse_simple_yaml(cfg_path.read_text(encoding="utf-8"))


def load_llm_settings(cfg: dict[str, Any] | None = None) -> LLMSettings:
    cfg = cfg or load_config()
    llm = dict(cfg.get("llm") or {})
    provider = (
        os.getenv("AI_ANALYST_PROVIDER")
        or os.getenv("ME_AI_PROVIDER")
        or str(llm.get("provider") or "template")
    ).strip().lower()
    model = (
        os.getenv("AI_ANALYST_MODEL")
        or os.getenv("ME_AI_MODEL")
        or str(llm.get("model") or "template")
    ).strip()
    base_url = (
        os.getenv("AI_ANALYST_BASE_URL")
        or os.getenv("ME_AI_BASE_URL")
        or (str(llm["base_url"]) if llm.get("base_url") else None)
    )
    if base_url:
        base_url = base_url.strip() or None

    api_key = None
    if provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("AI_ANALYST_API_KEY")
    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AI_ANALYST_API_KEY") or os.getenv("ME_AI_API_KEY")
    elif provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("AI_ANALYST_API_KEY")
        if not base_url:
            base_url = "https://openrouter.ai/api/v1"
    elif provider == "ollama":
        if not base_url:
            base_url = "http://127.0.0.1:11434/v1"
        api_key = os.getenv("AI_ANALYST_API_KEY") or "ollama"
    else:
        api_key = os.getenv("AI_ANALYST_API_KEY") or os.getenv("ME_AI_API_KEY")

    return LLMSettings(
        provider=provider,
        model=model,
        timeout_sec=float(
            os.getenv("AI_ANALYST_TIMEOUT_SEC")
            or llm.get("timeout_sec")
            or 90
        ),
        max_retries=int(
            os.getenv("AI_ANALYST_MAX_RETRIES")
            or llm.get("max_retries")
            or 2
        ),
        max_tokens=int(llm.get("max_tokens") or 4096),
        base_url=base_url,
        api_key=(api_key or "").strip() or None,
    )


DEFAULT_AGENTS: dict[str, AgentProfile] = {
    "s46_full": AgentProfile("s46_full", "full_report", "market_report.md"),
    "s46_morning": AgentProfile("s46_morning", "morning_brief", "morning_brief.md"),
    "s46_evening": AgentProfile("s46_evening", "evening_brief", "evening_brief.md"),
    "s46_btc": AgentProfile("s46_btc", "btc_brief", "btc_brief.md"),
    "s46_macro": AgentProfile("s46_macro", "macro_brief", "macro_brief.md"),
    "s46_sp500": AgentProfile("s46_sp500", "sp500_brief", "sp500_brief.md"),
    "s46_x": AgentProfile("s46_x", "x_post", "x_post.md"),
    "s46_telegram": AgentProfile("s46_telegram", "telegram_post", "telegram_post.md"),
    "s46_json": AgentProfile("s46_json", "json_summary", "market_summary.json"),
    "s47_paper_signal": AgentProfile("s47_paper_signal", "paper_signal", "paper_signal.json"),
}


def load_agent_profiles(cfg: dict[str, Any] | None = None) -> dict[str, AgentProfile]:
    cfg = cfg or load_config()
    out = dict(DEFAULT_AGENTS)
    for agent_id, row in (cfg.get("agents") or {}).items():
        if not isinstance(row, dict):
            continue
        prompt = str(row.get("prompt") or "").strip()
        output = str(row.get("output") or "").strip()
        if prompt and output:
            out[str(agent_id)] = AgentProfile(str(agent_id), prompt, output)
    return out


def load_telegram_terminal_settings(cfg: dict[str, Any] | None = None) -> TelegramTerminalSettings:
    cfg = cfg or load_config()
    tg = dict(cfg.get("telegram") or {})
    return TelegramTerminalSettings(
        report_cache_minutes=int(
            os.getenv("AI_ANALYST_REPORT_CACHE_MINUTES")
            or tg.get("report_cache_minutes")
            or 15
        ),
        enable_progress_messages=str(
            os.getenv("AI_ANALYST_ENABLE_PROGRESS")
            or tg.get("enable_progress_messages")
            or "true"
        ).strip().lower() not in ("0", "false", "no"),
        default_report=str(
            os.getenv("AI_ANALYST_DEFAULT_REPORT")
            or tg.get("default_report")
            or "full"
        ).strip().lower(),
        parse_mode=str(
            os.getenv("AI_ANALYST_TELEGRAM_PARSE_MODE")
            or tg.get("parse_mode")
            or "HTML"
        ).strip().upper(),
    )


REPORT_ARTIFACTS: dict[str, str] = {
    "market": "market_report.md",
    "btc": "btc_brief.md",
    "macro": "macro_brief.md",
    "sp500": "sp500_brief.md",
    "telegram": "telegram_post.md",
    "context": "market_context.json",
    "summary": "market_summary.json",
}
