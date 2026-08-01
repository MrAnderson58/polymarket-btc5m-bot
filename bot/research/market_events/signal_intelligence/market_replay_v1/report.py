"""Reports for Market Replay Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "market_replay_v1"
REPORT_MD = BASE_DIR / "REPLAY_REPORT.md"
LIBRARY_MD = BASE_DIR / "REPLAY_LIBRARY.md"
SIMILARITY_MD = BASE_DIR / "SIMILARITY_REPORT.md"


def format_replay_report(result: dict[str, Any]) -> str:
    miss = result.get("missing_data_report") or {}
    lines = [
        "# REPLAY_REPORT",
        "",
        "_Market Replay Engine V1 — reconstruct CLOSED trades as candle movies. "
        "Research only. Gate / Strategy / Paper / Optimizer / Execution unchanged._",
        "",
        "## Run",
        "",
        f"- trades analyzed: **{result.get('n_rows')}**",
        f"- replays built: **{result.get('n_replays')}**",
        f"- coverage (≥50% quality): **{result.get('replay_coverage_pct')}%**",
        f"- mean quality: **{result.get('mean_quality')}**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- lake source: `{(result.get('load_stats') or {}).get('source')}`",
        f"- candles preloaded: {(result.get('load_stats') or {}).get('n_candles')}",
        f"- G3 snapshots: {(result.get('load_stats') or {}).get('n_snapshots_g3')}",
        "",
        "## Missing data report",
        "",
        f"```json\n{json.dumps(miss.get('entry_field_missing_share') or {}, indent=2)}\n```",
        "",
        "## Top recurring replay patterns",
        "",
    ]
    for p in (result.get("patterns") or [])[:15]:
        lines.append(
            f"- n={p.get('count')} `{p.get('pattern')}` "
            f"examples={p.get('example_trade_ids')}"
        )
    if not (result.get("patterns") or []):
        lines.append("- (none)")
    lines.extend(["", "## Replay examples", ""])
    for ex in (result.get("examples") or [])[:3]:
        lines.append(f"### trade_id={ex.get('trade_id')}")
        lines.append("")
        excerpt = ex.get("replay_excerpt") or {}
        lines.append(f"- quality: {excerpt.get('quality')}")
        lines.append(f"- frames: {excerpt.get('timeline_labels')}")
        entry = excerpt.get("entry_frame") or {}
        lines.append(
            f"- ENTRY price={entry.get('price')} rsi={entry.get('rsi')} "
            f"funding={entry.get('funding')} regime={entry.get('regime')}"
        )
        lines.append(f"- liquidity: `{json.dumps(excerpt.get('liquidity') or {}, default=str)}`")
        lines.append("")
    lines.extend([
        "## Integrity",
        "",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- optimizer_unchanged: {result.get('optimizer_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- no_n_plus_1_sql: {result.get('no_n_plus_1_sql')}",
        "",
    ])
    return "\n".join(lines)


def format_library_md(result: dict[str, Any]) -> str:
    lines = [
        "# REPLAY_LIBRARY",
        "",
        f"SQLite `{result.get('library_table', 'market_trade_replays_v1')}` — "
        f"upserted **{result.get('library_upserted', 0)}** rows.",
        "",
        "| trade_id | regime | quality | similar_n |",
        "|---|---|---|---|",
    ]
    for r in (result.get("library_rows") or result.get("top20_replays") or [])[:50]:
        sim = r.get("similar_ids")
        if isinstance(sim, str):
            try:
                sim = json.loads(sim)
            except Exception:
                sim = []
        lines.append(
            f"| {r.get('trade_id')} | {r.get('regime')} | {r.get('quality')} | "
            f"{len(sim or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_similarity_md(result: dict[str, Any]) -> str:
    acc = result.get("similarity_accuracy") or {}
    lines = [
        "# SIMILARITY_REPORT",
        "",
        "TOP-100 neighbors via Euclidean / Cosine / DTW / Sequence similarity.",
        "",
        f"- accuracy proxy: `{acc.get('metric')}` = **{acc.get('hit_rate')}** "
        f"(n_pairs={acc.get('n_pairs')})",
        f"- similar_k: {result.get('similar_k')}",
        "",
    ]
    for ex in (result.get("examples") or [])[:3]:
        lines.append(f"## trade_id={ex.get('trade_id')}")
        lines.append("")
        metrics = ex.get("metrics") or {}
        for name, rows in metrics.items():
            lines.append(f"### {name}")
            lines.append("")
            for i, row in enumerate((rows or [])[:10], 1):
                lines.append(
                    f"{i}. id={row.get('trade_id')} dist={row.get('distance')} "
                    f"{row.get('symbol')} {row.get('direction')} pnl={row.get('pnl')}"
                )
            lines.append("")
    return "\n".join(lines)


def write_artifacts(
    result: dict[str, Any],
    *,
    replays: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    report = format_replay_report(result)
    REPORT_MD.write_text(report, encoding="utf-8")
    (OUT_DIR / "REPLAY_REPORT.md").write_text(report, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)

    lib_md = format_library_md(result)
    LIBRARY_MD.write_text(lib_md, encoding="utf-8")
    (OUT_DIR / "REPLAY_LIBRARY.md").write_text(lib_md, encoding="utf-8")
    paths["library_md"] = str(LIBRARY_MD)

    sim_md = format_similarity_md(result)
    SIMILARITY_MD.write_text(sim_md, encoding="utf-8")
    (OUT_DIR / "SIMILARITY_REPORT.md").write_text(sim_md, encoding="utf-8")
    paths["similarity_md"] = str(SIMILARITY_MD)

    # JSON artifacts (slim)
    slim_replays = []
    for r in (replays or result.get("top20_replays") or [])[:500]:
        slim_replays.append({
            "trade_id": r.get("trade_id"),
            "quality": r.get("quality"),
            "regime": r.get("regime"),
            "similar_ids": (r.get("similar_ids") or [])[:20],
            "liquidity": r.get("liquidity"),
            "missing_report": r.get("missing_report"),
            "timeline": r.get("timeline"),
            "n_frames": len(r.get("market_frames") or []),
            "n_events": len(r.get("events") or []),
        })
    payloads = {
        "replays_slim.json": slim_replays,
        "patterns.json": result.get("patterns") or [],
        "missing_data.json": result.get("missing_data_report") or {},
        "similarity_accuracy.json": result.get("similarity_accuracy") or {},
        "examples.json": result.get("examples") or [],
        "summary.json": {
            "n_rows": result.get("n_rows"),
            "n_replays": result.get("n_replays"),
            "elapsed_sec": result.get("elapsed_sec"),
            "replay_coverage_pct": result.get("replay_coverage_pct"),
            "mean_quality": result.get("mean_quality"),
            "library_upserted": result.get("library_upserted"),
        },
    }
    for name, payload in payloads.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)
    return paths


__all__ = [
    "OUT_DIR",
    "format_library_md",
    "format_replay_report",
    "format_similarity_md",
    "write_artifacts",
]
