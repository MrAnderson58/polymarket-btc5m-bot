"""Task G — multi-intent structured extraction (deterministic + AI shadow slot)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.db import connection_is_postgres
from bot.research.futures_agent.research_taxonomy import _infer_direction, _signal_symbols
from bot.research.futures_agent.research_utils import extract_research_symbols
from bot.research.futures_agent.signal_level_extract import extract_signal_levels, normalize_signal_text

INTENT_LABELS = (
    "EXPLICIT_SIGNAL",
    "MARKET_THESIS",
    "NEWS_EVENT",
    "CATALYST",
    "EXPECTED_PATH",
    "INVALIDATION",
    "TECHNICAL_LEVEL",
    "POSITION_SIZING",
    "LEVERAGE",
    "PERFORMANCE_UPDATE",
)

_RE_NEWS = re.compile(
    r"(?i)\b(breaking|news|sec|etf|fed|cpi|hack|listing|approval|announc)\b",
)
_RE_CATALYST = re.compile(
    r"(?i)\b(launch|unlock|airdrop|vote|upgrade|mainnet|partnership|merge)\b",
)
_RE_PATH = re.compile(
    r"(?i)\b(target|expect|should|reach|dump|pump|bounce|rally|grind|reclaim)\b|"
    r"тейк|цел[ьи]|ожида",
)
_RE_INVALIDATION = re.compile(
    r"(?i)(?:invalidat|if\s+.+\s+(?:fails?|breaks?)|unless|stop\s+if|"
    r"стоп|stop(?:\s*loss)?|\bsl\b)",
)
_RE_TECH = re.compile(
    r"(?i)(?:support|resistance|поддержк|сопротивлен|level|уровн|fib|ema|sma)",
)
_RE_LEVERAGE = re.compile(
    r"(?i)(?:"
    r"(\d+)\s*(?:x|х)\b|"
    r"(?:с|with|at)\s+(\d+)\s*(?:плеч|leverage|lev)\w*"
    r")",
)
_RE_POSITION_USD = re.compile(
    r"(?i)(?:"
    r"на\s+(\d+(?:\.\d+)?)\s*(?:доллар|usd|usdt|\$)|"
    r"(\d+(?:\.\d+)?)\s*(?:usd|usdt|\$)\b|"
    r"\$\s*(\d+(?:\.\d+)?)"
    r")",
)
_RE_BANK = re.compile(
    r"(?i)(?:банк|bank|equity|balance|депозит|deposit)\s*(?:уже|is|now|=|:)?\s*(\d+(?:\.\d+)?)",
)


@dataclass
class IntentExtraction:
    label: str
    extractor: str
    payload: dict[str, Any]
    confidence: float = 0.0


@dataclass
class MultiIntentResult:
    source_type: str
    source_record_id: str
    raw_text: str
    intents: list[IntentExtraction] = field(default_factory=list)


def extract_deterministic_intents(text: str) -> list[IntentExtraction]:
    """Extract explicit multi-label intents without overwriting raw text."""
    normalized = normalize_signal_text(text)
    syms = _signal_symbols(text) or extract_research_symbols(text)
    direction = _infer_direction(text)
    levels = extract_signal_levels(normalized)
    intents: list[IntentExtraction] = []

    if syms and direction and (
        levels.entry_low is not None or levels.stop is not None or levels.targets
    ):
        intents.append(IntentExtraction(
            label="EXPLICIT_SIGNAL",
            extractor="deterministic",
            confidence=0.92,
            payload={
                "symbols": syms,
                "direction": direction,
                "entry": levels.entry_low if levels.entry_low == levels.entry_high else [levels.entry_low, levels.entry_high],
                "targets": levels.targets,
                "stop": levels.stop,
            },
        ))
    elif syms and direction:
        intents.append(IntentExtraction(
            label="EXPLICIT_SIGNAL",
            extractor="deterministic",
            confidence=0.75,
            payload={"symbols": syms, "direction": direction},
        ))

    if _RE_NEWS.search(text):
        intents.append(IntentExtraction(
            label="NEWS_EVENT", extractor="deterministic", confidence=0.7,
            payload={"symbols": syms},
        ))
    if _RE_CATALYST.search(text):
        intents.append(IntentExtraction(
            label="CATALYST", extractor="deterministic", confidence=0.65,
            payload={"symbols": syms},
        ))
    if _RE_PATH.search(text) or levels.targets:
        intents.append(IntentExtraction(
            label="EXPECTED_PATH", extractor="deterministic", confidence=0.7,
            payload={"targets": levels.targets, "symbols": syms},
        ))
    if _RE_INVALIDATION.search(text) or levels.stop is not None:
        intents.append(IntentExtraction(
            label="INVALIDATION", extractor="deterministic", confidence=0.72,
            payload={"stop": levels.stop},
        ))
    if _RE_TECH.search(text) or levels.support or levels.resistance:
        intents.append(IntentExtraction(
            label="TECHNICAL_LEVEL", extractor="deterministic", confidence=0.68,
            payload={"support": levels.support, "resistance": levels.resistance},
        ))

    lev_m = _RE_LEVERAGE.search(text)
    if lev_m:
        lev = lev_m.group(1) or lev_m.group(2)
        intents.append(IntentExtraction(
            label="LEVERAGE", extractor="deterministic", confidence=0.85,
            payload={"leverage": int(lev) if lev else None},
        ))

    pos_m = _RE_POSITION_USD.search(text)
    if pos_m:
        amt = pos_m.group(1) or pos_m.group(2) or pos_m.group(3)
        intents.append(IntentExtraction(
            label="POSITION_SIZING", extractor="deterministic", confidence=0.82,
            payload={"amount_usd": float(amt) if amt else None},
        ))

    bank_m = _RE_BANK.search(text)
    if bank_m:
        intents.append(IntentExtraction(
            label="PERFORMANCE_UPDATE", extractor="deterministic", confidence=0.8,
            payload={"bank_usd": float(bank_m.group(1))},
        ))

    if syms and not any(i.label == "EXPLICIT_SIGNAL" for i in intents):
        if direction or _RE_PATH.search(text):
            intents.append(IntentExtraction(
                label="MARKET_THESIS", extractor="deterministic", confidence=0.6,
                payload={"symbols": syms, "direction": direction},
            ))

    return intents


def analyze_multi_intent(text: str, *, source_type: str, source_record_id: str) -> MultiIntentResult:
    return MultiIntentResult(
        source_type=source_type,
        source_record_id=source_record_id,
        raw_text=text,
        intents=extract_deterministic_intents(text),
    )


def insert_multi_intent(conn: Any, *, source_type: str, source_record_id: str,
                        label: str, extractor: str, payload: dict,
                        confidence: float) -> bool:
    now = int(time.time())
    params = (
        source_type, source_record_id, label, extractor,
        json.dumps(payload), confidence, now,
    )
    if connection_is_postgres(conn):
        row = conn.execute(
            """
            INSERT INTO futures_agent_post_multi_intent (
              source_type, source_record_id, label, extractor,
              structured_json, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_type, source_record_id, label, extractor) DO NOTHING
            RETURNING id
            """,
            params,
        ).fetchone()
        return row is not None
    conn.execute(
        """
        INSERT OR IGNORE INTO futures_agent_post_multi_intent (
          source_type, source_record_id, label, extractor,
          structured_json, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        params,
    )
    return True


def persist_multi_intent(conn: Any, result: MultiIntentResult) -> int:
    n = 0
    for intent in result.intents:
        if insert_multi_intent(
            conn,
            source_type=result.source_type,
            source_record_id=result.source_record_id,
            label=intent.label,
            extractor=intent.extractor,
            payload=intent.payload,
            confidence=intent.confidence,
        ):
            n += 1
    return n


def run_multi_intent_audit(conn: Any, *, limit: int = 50) -> str:
    """Audit bridged posts + recent trader posts for multi-intent coverage."""
    rows = conn.execute(
        """
        SELECT id, raw_text, channel_name FROM futures_agent_trader_posts
        ORDER BY message_ts DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    lines = [
        "TELEGRAM MULTI-INTENT AUDIT",
        "mode: SHADOW — deterministic extraction only; raw text preserved",
        f"posts_sampled: {len(rows)}",
        "",
    ]
    label_counts: dict[str, int] = {}
    for row in rows:
        result = analyze_multi_intent(
            row["raw_text"] or "",
            source_type="trader_post",
            source_record_id=str(row["id"]),
        )
        for intent in result.intents:
            label_counts[intent.label] = label_counts.get(intent.label, 0) + 1
        if len(lines) < 25:
            labels = [i.label for i in result.intents]
            preview = (row["raw_text"] or "")[:80].replace("\n", " ")
            lines.append(f"  post={row['id']} labels={labels} preview={preview!r}")

    lines.append("")
    lines.append(f"label_counts: {label_counts}")
    return "\n".join(lines)
