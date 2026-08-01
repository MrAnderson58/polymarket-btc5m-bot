"""Research Lake V1 report artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    DATASET_VERSION,
    FEATURE_VERSION,
    SCHEMA_VERSION_LAKE,
)

REPORT_MD = BASE_DIR / "RESEARCH_LAKE_REPORT.md"
PROFILE_MD = BASE_DIR / "RESEARCH_LAKE_PROFILE.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "lake"


def format_lake_report(result: dict[str, Any]) -> str:
    health = result.get("health") or {}
    lines = [
        "# RESEARCH_LAKE_REPORT",
        "",
        "_Research Lake Builder V2 — streaming batches, preloaded joins, SQL profiling. "
        "No Gate / Optimizer / Strategy / Paper / Execution changes._",
        "",
        f"- Mode: **{result.get('mode')}**",
        f"- Builder: `{result.get('builder_version') or 'v2-streaming'}`",
        f"- Rows seen/inserted/updated/skipped: "
        f"{result.get('rows_seen')}/{result.get('rows_inserted')}/"
        f"{result.get('rows_updated')}/{result.get('rows_skipped')}",
        f"- dataset_version: `{result.get('dataset_version') or DATASET_VERSION}`",
        f"- feature_version: `{result.get('feature_version') or FEATURE_VERSION}`",
        f"- schema_version: `{result.get('schema_version') or SCHEMA_VERSION_LAKE}`",
        f"- Health: **{health.get('status')}** (lake={health.get('n_lake')} "
        f"s42_closed={health.get('n_s42_closed')} coverage={health.get('coverage_pct')}%)",
        f"- Elapsed: {result.get('elapsed_sec')}s",
        f"- no_select_in_trade_loop: {result.get('no_select_in_trade_loop')}",
        "",
        "## Integrity",
        "",
        f"- duplicates: {health.get('duplicates')}",
        f"- missing_s55_joins: {health.get('missing_s55_joins')}",
        f"- missing_pnl: {health.get('missing_pnl')}",
        f"- null_feature_rows: {health.get('null_feature_rows')}",
        f"- broken_feature_rows: {health.get('broken_feature_rows')}",
        f"- schema_drift: {health.get('schema_drift')}",
        "",
        "## Issues",
        "",
    ]
    issues = health.get("issues") or []
    if not issues:
        lines.append("- (none)")
    else:
        for i in issues:
            lines.append(f"- {i}")
    lines.extend([
        "",
        "## Consumers",
        "",
        "Alpha / Optimizer / ML / Feature Information / Math Research / Edge Discovery "
        "should load via `load_research_lake_rows` (Research Lake only).",
        "",
        "Artifacts under `reports/research/lake/`.",
        "",
    ])
    return "\n".join(lines)


def format_lake_schema_md() -> str:
    return "\n".join([
        "# Research Lake Schema V1",
        "",
        f"- schema_version: `{SCHEMA_VERSION_LAKE}`",
        f"- dataset_version: `{DATASET_VERSION}`",
        f"- feature_version: `{FEATURE_VERSION}`",
        "",
        "## Table `market_events_research_lake_v1`",
        "",
        "| Column | Meaning |",
        "|---|---|",
        "| trade_id | S42 paper trade id (PK) |",
        "| symbol / direction | Instrument + side |",
        "| entry / exit / result / pnl | Outcome fields |",
        "| gate / confidence / regime | Decision context |",
        "| features_json | Numeric/categorical feature blob |",
        "| macro_json | Funding / OI / Fear / macro |",
        "| news_json | News / AI scores |",
        "| patterns_json | Pattern / regime / G31 / S56 |",
        "| alpha_labels_json | Alpha validation/discovery labels |",
        "| optimizer_state_json | Optimizer ops snapshot |",
        "| experiment_state_json | Recent experiments |",
        "| feature_version / dataset_version / schema_version | Version triad |",
        "",
        "## Sources joined",
        "",
        "- S42 paper trades (hub)",
        "- S55 trade features",
        "- S56 postmortem snapshots",
        "- G31 candidates (time/symbol proximity)",
        "- Feature Store extract_sample",
        "- Alpha / optimizer / experiment state (best-effort)",
        "",
    ])


def format_lake_statistics_md(result: dict[str, Any]) -> str:
    health = result.get("health") or {}
    return "\n".join([
        "# Research Lake Statistics",
        "",
        f"- build mode: {result.get('mode')}",
        f"- rows_seen: {result.get('rows_seen')}",
        f"- rows_inserted: {result.get('rows_inserted')}",
        f"- rows_updated: {result.get('rows_updated')}",
        f"- rows_skipped: {result.get('rows_skipped')}",
        f"- n_lake: {health.get('n_lake')}",
        f"- n_s42_closed: {health.get('n_s42_closed')}",
        f"- coverage_pct: {health.get('coverage_pct')}",
        f"- missing_s55_joins: {health.get('missing_s55_joins')}",
        f"- elapsed_sec: {result.get('elapsed_sec')}",
        "",
    ])


def format_lake_profile_md(result: dict[str, Any]) -> str:
    profile = result.get("profile") or {}
    explain = result.get("explain_slow") or []
    indexes = result.get("indexes_ensured") or []
    lines = [
        "# RESEARCH_LAKE_PROFILE",
        "",
        "Research Lake Builder V2 — SQL profile for streaming builds.",
        "",
        "## Summary",
        "",
        f"- builder_version: `{result.get('builder_version')}`",
        f"- mode: `{result.get('mode')}`",
        f"- rows_seen / inserted / updated / skipped: "
        f"{result.get('rows_seen')} / {result.get('rows_inserted')} / "
        f"{result.get('rows_updated')} / {result.get('rows_skipped')}",
        f"- batch_size: {result.get('batch_size')}",
        f"- elapsed_sec (wall): {result.get('elapsed_sec')}",
        f"- n_queries profiled: {profile.get('n_queries')}",
        f"- total_sql_sec: {profile.get('total_sql_sec')}",
        f"- n_slow (>=1s): {profile.get('n_slow')}",
        f"- no_select_in_trade_loop: {result.get('no_select_in_trade_loop')}",
        "",
        "## Before / After",
        "",
        "| Metric | V1 (N+1 per trade) | V2 (streaming + preload) |",
        "|---|---|---|",
        "| Join strategy | SELECT S55/S56/G31/alpha inside trade loop | Preload dicts once; O(1)/O(log n) lookup |",
        "| S42 load | Full table / unbounded | `WHERE id > last_id LIMIT 1000` batches |",
        "| Inserts | Single end commit (or per-row) | Upsert + commit every 1000 rows |",
        "| Indexes | Missing on `s55.paper_trade_id` etc. | Auto `CREATE INDEX IF NOT EXISTS` on JOIN keys |",
        f"| Wall time (this run) | (N× queries → minutes in `sqlite3_step`) | **{result.get('elapsed_sec')}s** |",
    ]
    if result.get("benchmark_30k"):
        b = result["benchmark_30k"]
        lines.append(
            f"| 30k synthetic wall | V1 N+1: minutes–hours at scale | "
            f"**V2 {b.get('elapsed_sec')}s** (under_2_min={b.get('under_2_min')}) |"
        )
    lines.extend([
        "",
        "Target: 30k CLOSED trades under 2 minutes on Apple Silicon.",
        "",
        "## Indexes added / ensured",
        "",
    ])
    if not indexes:
        lines.append("- (none)")
    else:
        for name in indexes:
            lines.append(f"- `{name}`")
    lines.extend(["", "## Top 20 slow SQL", ""])
    top = profile.get("top_slow") or []
    if not top:
        lines.append("_No queries recorded._")
    else:
        for i, ev in enumerate(top, 1):
            lines.append(
                f"{i}. **{ev.get('elapsed_sec')}s** rows={ev.get('rows')} "
                f"params=`{ev.get('params') or ''}`"
            )
            lines.append("")
            lines.append("```sql")
            lines.append(str(ev.get("sql") or ""))
            lines.append("```")
            lines.append("")
    lines.extend(["", "## EXPLAIN QUERY PLAN (slow >= 1s, else top wall-time)", ""])
    if not explain:
        lines.append("_No queries to explain._")
    else:
        for i, item in enumerate(explain, 1):
            lines.append(
                f"### #{i} — {item.get('elapsed_sec')}s "
                f"(rows={item.get('rows')})"
            )
            lines.append("")
            lines.append("```sql")
            lines.append(str(item.get("sql") or ""))
            lines.append("```")
            lines.append("")
            lines.append("Plan:")
            lines.append("")
            for p in item.get("plan") or []:
                lines.append(f"- `{p}`")
            lines.append("")
    if result.get("benchmark_30k"):
        b = result["benchmark_30k"]
        lines.extend([
            "",
            "## Synthetic 30k benchmark",
            "",
            f"- rows: {b.get('rows')}",
            f"- elapsed_sec: {b.get('elapsed_sec')}",
            f"- under_2_min: {b.get('under_2_min')}",
            f"- v1_estimated_sec (N+1 G31/S55): {b.get('v1_estimated_sec', 'N× join SELECTs → minutes+')}",
            "",
        ])
    lines.append("")
    return "\n".join(lines)


def write_lake_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    health = result.get("health") or {}
    health_path = OUT_DIR / "lake_health.json"
    health_path.write_text(json.dumps(health, indent=2, default=str), encoding="utf-8")
    paths["lake_health.json"] = str(health_path)

    schema_md = format_lake_schema_md()
    schema_path = OUT_DIR / "lake_schema.md"
    schema_path.write_text(schema_md, encoding="utf-8")
    paths["lake_schema.md"] = str(schema_path)

    stats_md = format_lake_statistics_md(result)
    stats_path = OUT_DIR / "lake_statistics.md"
    stats_path.write_text(stats_md, encoding="utf-8")
    paths["lake_statistics.md"] = str(stats_path)

    md = format_lake_report(result)
    REPORT_MD.write_text(md, encoding="utf-8")
    copy = OUT_DIR / "RESEARCH_LAKE_REPORT.md"
    copy.write_text(md, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)
    paths["report_copy"] = str(copy)
    return paths


def write_lake_profile_report(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = format_lake_profile_md(result)
    PROFILE_MD.write_text(md, encoding="utf-8")
    copy = OUT_DIR / "RESEARCH_LAKE_PROFILE.md"
    copy.write_text(md, encoding="utf-8")
    profile_json = OUT_DIR / "lake_profile.json"
    profile_json.write_text(
        json.dumps(
            {
                "profile": result.get("profile"),
                "explain_slow": result.get("explain_slow"),
                "indexes_ensured": result.get("indexes_ensured"),
                "elapsed_sec": result.get("elapsed_sec"),
                "builder_version": result.get("builder_version"),
                "benchmark_30k": result.get("benchmark_30k"),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {
        "profile_md": str(PROFILE_MD),
        "profile_copy": str(copy),
        "profile_json": str(profile_json),
    }


__all__ = [
    "format_lake_report",
    "format_lake_profile_md",
    "write_lake_artifacts",
    "write_lake_profile_report",
]
