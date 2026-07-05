"""Parser quality audit — v1 vs v2 metrics on stratified sample."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.futures.config import (
    PARSER_VERSION,
    PARSER_VERSION_V2,
    REPARSE_MIN_EXPLICIT_PRECISION,
    REPARSE_MIN_SIDE_ACCURACY,
    REPARSE_MIN_SYMBOL_ACCURACY,
    VALIDATION_SAMPLE_SIZE,
)
from bot.research.futures.parser import parse_signal_text
from bot.research.futures.parser_v2 import extract_symbol_v2, parse_signal_v2
from bot.research.futures.source_reader import SourceReader, resolve_research_source
from bot.research.futures.taxonomy import MessageType, classify_message

STRATA_KEYWORDS = ("LONG", "вход", "стоп", "TP", "SL", "обзор", "review")


def _load_validation_sample(
    source: SourceReader,
    *,
    source_filter: str | None,
    target: int,
) -> list:
    seen: set[str] = set()
    msgs = []
    per = max(20, target // (len(STRATA_KEYWORDS) + 2))

    for kw in STRATA_KEYWORDS:
        for row in source.iter_raw_rows(source=source_filter, text_ilike=kw, limit=per, order="DESC"):
            msg = source.map_row(row)
            if msg and msg.message_id not in seen:
                seen.add(msg.message_id)
                msgs.append(msg)

    for order in ("DESC", "ASC"):
        for row in source.iter_raw_rows(source=source_filter, limit=per, order=order):
            msg = source.map_row(row)
            if msg and msg.message_id not in seen:
                seen.add(msg.message_id)
                msgs.append(msg)
            if len(msgs) >= target:
                break
        if len(msgs) >= target:
            break
    return msgs[:target]


def _audit_version(msgs: list, *, version: str) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    sym_ok = sym_total = 0
    side_ok = side_total = 0
    entry_cov = sl_cov = tp_cov = 0
    explicit_total = 0
    gate_pass = 0
    review_leaks = 0

    for msg in msgs:
        tax = classify_message(msg.text)
        is_explicit = tax.message_type == MessageType.EXPLICIT_SIGNAL
        is_review = tax.message_type == MessageType.MARKET_REVIEW

        if version == PARSER_VERSION_V2:
            result = parse_signal_v2(msg.text)
            parsed = result.parsed
            passed = result.passes_gate
        else:
            parsed = parse_signal_text(msg.text)
            passed = bool(parsed.side and parsed.symbol)

        if is_explicit:
            explicit_total += 1
            side_total += 1
            if parsed.side:
                side_ok += 1
            if parsed.symbol:
                sym_total += 1
                header_sym, _ = extract_symbol_v2(msg.text)
                if header_sym and parsed.symbol == header_sym:
                    sym_ok += 1
            if parsed.entry_min is not None:
                entry_cov += 1
            if parsed.stop_loss is not None:
                sl_cov += 1
            if parsed.take_profits:
                tp_cov += 1

        if passed:
            gate_pass += 1
            if is_explicit:
                tp += 1
            else:
                fp += 1
            if is_review:
                review_leaks += 1
        else:
            if is_explicit:
                fn += 1
            else:
                tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    return {
        "version": version,
        "sample_size": len(msgs),
        "explicit_taxonomy_count": explicit_total,
        "gate_pass_count": gate_pass,
        "explicit_precision": round(precision, 4),
        "explicit_recall": round(recall, 4),
        "review_leaks": review_leaks,
        "symbol_accuracy": round(sym_ok / sym_total, 4) if sym_total else None,
        "side_accuracy": round(side_ok / side_total, 4) if side_total else None,
        "entry_coverage": round(entry_cov / explicit_total, 4) if explicit_total else None,
        "sl_coverage": round(sl_cov / explicit_total, 4) if explicit_total else None,
        "tp_coverage": round(tp_cov / explicit_total, 4) if explicit_total else None,
    }


def run_parser_audit(
    *,
    research_conn: sqlite3.Connection | None = None,
    source: SourceReader | None = None,
    source_filter: str | None = None,
    sample_size: int = VALIDATION_SAMPLE_SIZE,
) -> dict[str, Any]:
    if source is None:
        assert research_conn is not None
        source, owns = resolve_research_source(research_conn)
    else:
        owns = False

    try:
        msgs = _load_validation_sample(source, source_filter=source_filter, target=sample_size)
        v1 = _audit_version(msgs, version=PARSER_VERSION)
        v2 = _audit_version(msgs, version=PARSER_VERSION_V2)

        safe = (
            (v2["explicit_precision"] or 0) >= REPARSE_MIN_EXPLICIT_PRECISION
            and (v2["symbol_accuracy"] or 0) >= REPARSE_MIN_SYMBOL_ACCURACY
            and (v2["side_accuracy"] or 0) >= REPARSE_MIN_SIDE_ACCURACY
            and v2["review_leaks"] == 0
        )

        return {
            "source_filter": source_filter,
            "validation_sample_size": len(msgs),
            "v1": v1,
            "v2": v2,
            "v1_failure_analysis": {
                "partial_from_side_or_symbol_only": v1["gate_pass_count"] - v1["explicit_taxonomy_count"],
                "review_leaks": v1["review_leaks"],
                "low_precision": v1["explicit_precision"],
            },
            "safe_to_reparse_all": safe,
            "reparse_gates": {
                "explicit_precision": REPARSE_MIN_EXPLICIT_PRECISION,
                "symbol_accuracy": REPARSE_MIN_SYMBOL_ACCURACY,
                "side_accuracy": REPARSE_MIN_SIDE_ACCURACY,
            },
            "recommendation": (
                "SAFE to parse all channel messages with deterministic_v2"
                if safe else
                "NOT SAFE for full 7530 reparse — continue parser tuning / manual label review"
            ),
        }
    finally:
        if owns:
            source.close()


def render_parser_audit(report: dict[str, Any]) -> str:
    lines = [
        "PARSER QUALITY AUDIT",
        "=" * 40,
        f"source: {report.get('source_filter') or '(all)'}",
        f"validation sample: {report['validation_sample_size']}",
        "",
        "V1 METRICS",
    ]
    for k, v in report["v1"].items():
        lines.append(f"  {k}: {v}")
    lines.extend(["", "V2 METRICS"])
    for k, v in report["v2"].items():
        lines.append(f"  {k}: {v}")
    lines.extend([
        "",
        "V1 FAILURE ANALYSIS",
    ])
    for k, v in report["v1_failure_analysis"].items():
        lines.append(f"  {k}: {v}")
    lines.extend([
        "",
        f"SAFE TO REPARSE ALL: {report['safe_to_reparse_all']}",
        f"RECOMMENDATION: {report['recommendation']}",
    ])
    return "\n".join(lines)
