"""Read-only production PostgreSQL audit for Futures Agent Stage 3.

Run on Mac Mini where FUTURES_SOURCE_DATABASE_URL is configured:

  python -m bot.research.futures_agent stage3-audit
  python -m bot.research.futures_agent stage3-audit --write docs/research/STAGE3_DATA_AUDIT.md

Never writes to source tables. Optional --write updates the audit markdown file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.futures.db_config import get_futures_source_database_url
from bot.research.futures.taxonomy import MessageType, classify_message
from bot.research.futures_agent.env_bootstrap import bootstrap_config, project_root

AUDIT_TABLES = (
    "telegram_messages",
    "news",
    "source_ratings",
    "telegram_channels",
    "telegram_signals",
)

RESEARCH_TYPES = frozenset({
    MessageType.MARKET_REVIEW.value,
    MessageType.MARKET_COMMENTARY.value,
    MessageType.NEWS.value,
})


def _connect():
    bootstrap_config()
    url = get_futures_source_database_url()
    if not url:
        raise RuntimeError(
            "FUTURES_SOURCE_DATABASE_URL (or TELEGRAM_DATABASE_URL) not set. "
            "Configure .env on Mac Mini before running stage3-audit."
        )
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary required") from exc
    conn = psycopg2.connect(url, connect_timeout=15)
    conn.set_session(readonly=True, autocommit=True)
    return conn, RealDictCursor


def _table_exists(cur, name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (name,),
    )
    return cur.fetchone() is not None


def _columns(cur, name: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable,
               character_maximum_length, numeric_precision
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (name,),
    )
    return [dict(r) for r in cur.fetchall()]


def _count(cur, table: str, where: str = "", params: tuple = ()) -> int:
    q = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        q += f" WHERE {where}"
    cur.execute(q, params)
    return int(cur.fetchone()["n"])


def _null_rate(cur, table: str, col: str) -> float:
    total = _count(cur, table)
    if not total:
        return 0.0
    nulls = _count(cur, table, f"{col} IS NULL")
    return nulls / total


def _audit_telegram_messages(cur) -> dict[str, Any]:
    table = "telegram_messages"
    cols = _columns(cur, table)
    col_names = {c["column_name"] for c in cols}
    n = _count(cur, table)
    cur.execute(f"SELECT MIN(message_date) AS mn, MAX(message_date) AS mx FROM {table}")
    ts = cur.fetchone()
    cur.execute(
        f"SELECT channel_name, COUNT(*) AS n FROM {table} "
        f"GROUP BY channel_name ORDER BY n DESC LIMIT 40"
    )
    top_channels = [dict(r) for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(DISTINCT channel_name) AS n FROM {table}")
    distinct_channels = int(cur.fetchone()["n"])

    null_rates = {}
    for col in ("message_text", "channel_name", "telegram_message_id", "message_date", "collected_at"):
        if col in col_names:
            null_rates[col] = round(_null_rate(cur, table, col), 6)

    cur.execute(
        f"""
        SELECT COUNT(*) AS dup_groups FROM (
          SELECT telegram_message_id, channel_name, COUNT(*) AS c
          FROM {table}
          GROUP BY telegram_message_id, channel_name
          HAVING COUNT(*) > 1
        ) x
        """
    )
    dup_id_channel = int(cur.fetchone()["dup_groups"])

    cur.execute(
        f"""
        SELECT message_text, channel_name, message_date, telegram_message_id
        FROM {table}
        WHERE message_text IS NOT NULL AND LENGTH(TRIM(message_text)) > 30
        ORDER BY message_date DESC
        LIMIT 25000
        """
    )
    sample_rows = cur.fetchall()
    tax_counts: Counter = Counter()
    examples: dict[str, list] = {t: [] for t in RESEARCH_TYPES | {"EXPLICIT_SIGNAL", "OTHER"}}
    forward_hints = repost_hints = dup_text = 0
    seen_norm: set[str] = set()
    for row in sample_rows:
        text = row["message_text"] or ""
        tr = classify_message(text)
        tax_counts[tr.message_type.value] += 1
        key = tr.message_type.value
        if key in examples and len(examples[key]) < 3:
            examples[key].append({
                "channel": row["channel_name"],
                "date": str(row["message_date"]),
                "telegram_message_id": row["telegram_message_id"],
                "preview": text[:300].replace("\n", " "),
            })
        tl = text.lower()
        if "forwarded from" in tl or "переслан" in tl:
            forward_hints += 1
        if "t.me/" in text[:200]:
            repost_hints += 1
        norm = re.sub(r"\s+", " ", text.strip().lower())[:400]
        if norm in seen_norm:
            dup_text += 1
        seen_norm.add(norm)

    sample_n = len(sample_rows)
    research_n = sum(tax_counts[t] for t in RESEARCH_TYPES)

    cur.execute(
        f"""
        (SELECT message_text FROM {table} WHERE message_text IS NOT NULL
         ORDER BY message_date ASC LIMIT 2500)
        UNION ALL
        (SELECT message_text FROM {table} WHERE message_text IS NOT NULL
         ORDER BY message_date DESC LIMIT 2500)
        """
    )
    strat_counts: Counter = Counter()
    for row in cur.fetchall():
        strat_counts[classify_message(row["message_text"]).message_type.value] += 1

    return {
        "row_count": n,
        "columns": cols,
        "timestamp_range": {"min": str(ts["mn"]), "max": str(ts["mx"])},
        "null_rates": null_rates,
        "distinct_channels": distinct_channels,
        "top_channels": top_channels,
        "duplicate_msg_id_channel_groups": dup_id_channel,
        "has_author_column": bool(col_names & {"author", "author_name", "sender", "from_user"}),
        "has_reply_metadata": bool(col_names & {"reply_to_message_id", "reply_to", "forward_from"}),
        "has_raw_json": bool(col_names & {"raw_json", "metadata_json", "payload"}),
        "taxonomy_recent_sample": {
            "sample_size": sample_n,
            "counts": dict(tax_counts),
            "research_types_count": research_n,
            "research_types_pct": round(100.0 * research_n / sample_n, 2) if sample_n else 0,
            "forward_hint_rate": round(forward_hints / sample_n, 4) if sample_n else 0,
            "repost_hint_rate": round(repost_hints / sample_n, 4) if sample_n else 0,
            "dup_text_rate": round(dup_text / sample_n, 4) if sample_n else 0,
            "examples": examples,
        },
        "taxonomy_stratified_5k": dict(strat_counts),
        "extrapolated_research_rows": int(n * research_n / sample_n) if sample_n else None,
    }


def _audit_generic_table(cur, table: str) -> dict[str, Any]:
    if not _table_exists(cur, table):
        return {"exists": False}
    cols = _columns(cur, table)
    col_names = [c["column_name"] for c in cols]
    n = _count(cur, table)
    entry: dict[str, Any] = {"exists": True, "row_count": n, "columns": cols}
    for ts_col in ("published_at", "created_at", "timestamp", "message_date", "updated_at", "rated_at"):
        if ts_col in col_names:
            cur.execute(f"SELECT MIN({ts_col}) AS mn, MAX({ts_col}) AS mx FROM {table}")
            r = cur.fetchone()
            entry["timestamp_range"] = {"min": str(r["mn"]), "max": str(r["mx"])}
            break
    for col in col_names[:12]:
        entry.setdefault("null_rates", {})[col] = round(_null_rate(cur, table, col), 4)
    cur.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 3")
    entry["sample_rows"] = [dict(r) for r in cur.fetchall()]
    if "source" in col_names:
        cur.execute(f"SELECT COUNT(DISTINCT source) AS n FROM {table}")
        entry["distinct_sources"] = int(cur.fetchone()["n"])
    elif "channel_name" in col_names:
        cur.execute(f"SELECT COUNT(DISTINCT channel_name) AS n FROM {table}")
        entry["distinct_sources"] = int(cur.fetchone()["n"])
    return entry


def run_audit() -> dict[str, Any]:
    conn, cursor_factory = _connect()
    cur = conn.cursor(cursor_factory=cursor_factory)
    audit: dict[str, Any] = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "tables": {},
    }
    try:
        for table in AUDIT_TABLES:
            if table == "telegram_messages" and _table_exists(cur, table):
                audit["tables"][table] = _audit_telegram_messages(cur)
            else:
                audit["tables"][table] = _audit_generic_table(cur, table)
    finally:
        conn.close()
    return audit


def render_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Futures Agent Stage 3 — Production Data Audit",
        "",
        f"**Audited at (UTC):** {audit.get('audited_at_utc', 'unknown')}",
        "",
        "> Generated by `python -m bot.research.futures_agent stage3-audit --write`.",
        "> Source DB is read-only (`FUTURES_SOURCE_DATABASE_URL`).",
        "",
    ]
    for table, data in audit.get("tables", {}).items():
        lines.append(f"## `{table}`")
        lines.append("")
        if not data.get("exists", True) and data.get("exists") is False:
            lines.append("**Status:** table does not exist in `public` schema.")
            lines.append("")
            continue
        lines.append(f"- **Row count:** {data.get('row_count', 0):,}")
        if data.get("timestamp_range"):
            tr = data["timestamp_range"]
            lines.append(f"- **Timestamp range:** {tr.get('min')} → {tr.get('max')}")
        if data.get("distinct_channels"):
            lines.append(f"- **Distinct channels:** {data['distinct_channels']}")
        if data.get("distinct_sources"):
            lines.append(f"- **Distinct sources:** {data['distinct_sources']}")
        lines.append("")
        lines.append("### Columns")
        lines.append("")
        lines.append("| column | type | nullable |")
        lines.append("|--------|------|----------|")
        for col in data.get("columns", []):
            lines.append(
                f"| `{col['column_name']}` | {col['data_type']} | {col['is_nullable']} |"
            )
        lines.append("")
        if data.get("null_rates"):
            lines.append("### Null rates (key fields)")
            lines.append("")
            for k, v in data["null_rates"].items():
                lines.append(f"- `{k}`: {v:.2%}" if v <= 1 else f"- `{k}`: {v}")
            lines.append("")
        if data.get("top_channels"):
            lines.append("### Top channels (by message count)")
            lines.append("")
            for ch in data["top_channels"][:15]:
                lines.append(f"- `{ch.get('channel_name', ch.get('source'))}`: {ch['n']:,}")
            lines.append("")
        if data.get("taxonomy_recent_sample"):
            ts = data["taxonomy_recent_sample"]
            lines.append("### Taxonomy sample (recent messages)")
            lines.append("")
            lines.append(f"- Sample size: {ts['sample_size']:,}")
            lines.append(f"- Research types (REVIEW+COMMENTARY+NEWS): {ts['research_types_count']:,} "
                         f"({ts['research_types_pct']}%)")
            if data.get("extrapolated_research_rows"):
                lines.append(f"- Extrapolated research-like rows (full table): ~{data['extrapolated_research_rows']:,}")
            lines.append("")
            lines.append("| taxonomy | count |")
            lines.append("|----------|------:|")
            for k, v in sorted(ts["counts"].items(), key=lambda x: -x[1]):
                lines.append(f"| {k} | {v:,} |")
            lines.append("")
            for rtype, exs in ts.get("examples", {}).items():
                if not exs:
                    continue
                lines.append(f"#### Examples: {rtype}")
                lines.append("")
                for ex in exs:
                    lines.append(f"- **{ex['channel']}** ({ex['date']}): {ex['preview']}")
                lines.append("")
        if data.get("sample_rows"):
            lines.append("### Sample rows (latest 3)")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(data["sample_rows"], indent=2, default=str)[:4000])
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 3 production data audit (read-only)")
    parser.add_argument(
        "--write",
        type=str,
        default=None,
        help="Write markdown report to this path",
    )
    parser.add_argument("--json", type=str, default=None, help="Write raw JSON audit")
    args = parser.parse_args(argv)
    try:
        audit = run_audit()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        Path(args.json).write_text(json.dumps(audit, indent=2, default=str))
        print(f"Wrote {args.json}")
    md = render_markdown(audit)
    if args.write:
        out = Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"Wrote {out}")
    else:
        print(md)
    return 0
