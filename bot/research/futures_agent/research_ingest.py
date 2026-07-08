"""Chunked research post ingestion from telegram source tables."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator

from bot.research.futures.source_data import (
    MessageColumnMap,
    resolve_message_columns,
    row_to_raw_message,
)
from bot.research.futures_agent.source_requirements import open_stage3_source_reader
from bot.research.futures_agent.db import connection_is_postgres, insert_returning_id, validate_write_table
from bot.research.futures_agent.research_taxonomy import classify_research_content
from bot.research.futures_agent.research_utils import (
    content_hash,
    extract_symbols,
    message_ts_to_epoch,
    symbols_json,
)

DEFAULT_CHUNK_SIZE = 2000
DEFAULT_MAX_PER_SOURCE: int | None = None


@dataclass
class IngestResearchStats:
    scanned: int = 0
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_empty: int = 0
    skipped_hash_duplicate: int = 0
    skipped_source_cap: int = 0
    per_channel: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    content_type_counts: Counter = field(default_factory=Counter)
    symbols_extracted: int = 0
    directions_extracted: int = 0
    levels_extracted: int = 0
    current_class: str = ""
    total_estimate: int | None = None


def _reader_is_postgres(reader: SourceReader) -> bool:
    return getattr(reader, "info", None) is not None and reader.info.backend == "postgres"


def _resolve_source_mapping(
    reader: SourceReader,
    source_table: str,
) -> MessageColumnMap | None:
    tables = reader.list_source_tables()
    info = tables.get(source_table)
    if not info or not info.get("exists"):
        resolved = reader.resolve_message_table()
        return resolved[1] if resolved else None
    cols = info.get("columns") or []
    return resolve_message_columns(source_table, cols)


def _build_ts_filtered_query(
    mapping: MessageColumnMap,
    *,
    source: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order: str = "ASC",
    param_style: str = "pg",
) -> tuple[str, list[Any]]:
    placeholder = "%s" if param_style == "pg" else "?"
    q = f"SELECT {mapping.select_sql()} FROM {mapping.table}"
    params: list[Any] = []
    clauses: list[str] = []
    if source and mapping.source_col:
        clauses.append(f"{mapping.source_col} = {placeholder}")
        params.append(source)
    if start_ts is not None:
        if param_style == "pg":
            clauses.append(
                f"EXTRACT(EPOCH FROM {mapping.ts_col}::timestamptz) >= {placeholder}"
            )
        else:
            clauses.append(f"CAST({mapping.ts_col} AS INTEGER) >= {placeholder}")
        params.append(start_ts)
    if end_ts is not None:
        if param_style == "pg":
            clauses.append(
                f"EXTRACT(EPOCH FROM {mapping.ts_col}::timestamptz) <= {placeholder}"
            )
        else:
            clauses.append(f"CAST({mapping.ts_col} AS INTEGER) <= {placeholder}")
        params.append(end_ts)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += f" ORDER BY {mapping.ts_col} {order}"
    if limit is not None:
        q += f" LIMIT {placeholder}"
        params.append(int(limit))
    if offset is not None:
        q += f" OFFSET {placeholder}"
        params.append(int(offset))
    return q, params


def _iter_mapped_rows(
    reader: SourceReader,
    mapping: MessageColumnMap,
    *,
    source: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order: str = "ASC",
) -> Iterator[dict[str, Any]]:
    postgres = _reader_is_postgres(reader)
    param_style = "pg" if postgres else "sqlite"
    q, params = _build_ts_filtered_query(
        mapping,
        source=source,
        start_ts=start_ts,
        end_ts=end_ts,
        limit=limit,
        offset=offset,
        order=order,
        param_style=param_style,
    )
    if postgres:
        cur = reader._conn.cursor()  # type: ignore[attr-defined]
        cur.execute(q, params)
        for row in cur:
            yield dict(row)
        return
    conn = reader._conn  # type: ignore[attr-defined]
    import sqlite3
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(q, params):
            yield dict(row)
    finally:
        conn.row_factory = prev_factory


def iter_source_messages(
    reader: SourceReader,
    *,
    source_table: str = "telegram_messages",
    channel: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized source rows in chunks without loading full corpus."""
    mapping = _resolve_source_mapping(reader, source_table)
    if mapping is None:
        return

    offset = 0
    yielded = 0
    while True:
        if limit is not None and yielded >= limit:
            break
        batch_limit = chunk_size
        if limit is not None:
            batch_limit = min(chunk_size, limit - yielded)
        rows = list(
            _iter_mapped_rows(
                reader,
                mapping,
                source=channel,
                start_ts=start_ts,
                end_ts=end_ts,
                limit=batch_limit,
                offset=offset,
                order="ASC",
            ),
        )
        if not rows:
            break
        for row in rows:
            msg = row_to_raw_message(row, mapping)
            if msg is None:
                continue
            ts = message_ts_to_epoch(row.get(mapping.ts_col)) or msg.timestamp
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue
            yield {
                "source_message_id": str(msg.message_id),
                "channel_name": msg.source,
                "message_ts": ts,
                "raw_text": msg.text,
            }
            yielded += 1
            if limit is not None and yielded >= limit:
                return
        offset += len(rows)
        if len(rows) < batch_limit:
            break


def _existing_hashes(conn: Any, hashes: set[str]) -> set[str]:
    if not hashes:
        return set()
    found: set[str] = set()
    items = list(hashes)
    for i in range(0, len(items), 500):
        chunk = items[i:i + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT content_hash FROM futures_agent_trader_posts WHERE content_hash IN ({placeholders})",
            chunk,
        ).fetchall()
        found.update(r["content_hash"] for r in rows)
    return found


def _insert_post(
    conn: Any,
    *,
    source_message_id: str,
    channel_name: str,
    message_ts: int,
    raw_text: str,
    c_hash: str,
    content_type: str,
    symbols: list[str],
    confidence: float,
) -> int | None:
    validate_write_table("futures_agent_trader_posts")
    postgres = connection_is_postgres(conn)
    sym_payload = symbols_json(symbols)
    if postgres:
        row = conn.execute(
            """
            INSERT INTO futures_agent_trader_posts (
                source_message_id, channel_name, message_ts, raw_text,
                content_hash, content_type, symbols_json, deterministic_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?::jsonb, ?)
            ON CONFLICT (channel_name, source_message_id) DO NOTHING
            RETURNING id
            """,
            (
                source_message_id, channel_name, message_ts, raw_text,
                c_hash, content_type, sym_payload, confidence,
            ),
        ).fetchone()
        return int(row["id"]) if row else None

    try:
        return insert_returning_id(
            conn,
            """
            INSERT OR IGNORE INTO futures_agent_trader_posts (
                source_message_id, channel_name, message_ts, raw_text,
                content_hash, content_type, symbols_json, deterministic_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_message_id, channel_name, message_ts, raw_text,
                c_hash, content_type, sym_payload, confidence,
            ),
        )
    except Exception:
        return None


def _estimate_source_total(
    reader: SourceReader,
    *,
    channel: str | None,
    limit: int | None,
) -> int | None:
    if limit is not None:
        return limit
    try:
        rows = reader.channel_stats()
        if channel:
            for r in rows:
                if r.get("source") == channel:
                    return int(r.get("count", 0))
            return None
        return sum(int(r.get("count", 0)) for r in rows)
    except Exception:
        return None


def ingest_research_posts(
    conn: Any,
    *,
    source_table: str = "telegram_messages",
    channel: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_per_source: int | None = DEFAULT_MAX_PER_SOURCE,
    progress_every: int = 500,
) -> IngestResearchStats:
    """Ingest classified research posts into futures_agent_trader_posts."""
    from bot.research.futures_agent.research_reconciliation import count_extraction_coverage

    stats = IngestResearchStats()
    reader = open_stage3_source_reader()
    seen_hashes: set[str] = set()
    channel_inserted: dict[str, int] = defaultdict(int)
    t0 = time.monotonic()
    try:
        stats.total_estimate = _estimate_source_total(reader, channel=channel, limit=limit)
        batch_hashes: set[str] = set()
        batch_rows: list[dict[str, Any]] = []
        for row in iter_source_messages(
            reader,
            source_table=source_table,
            channel=channel,
            start_ts=start_ts,
            end_ts=end_ts,
            chunk_size=chunk_size,
            limit=limit,
        ):
            stats.scanned += 1
            ch = row["channel_name"]
            if max_per_source is not None and channel_inserted[ch] >= max_per_source:
                stats.skipped_source_cap += 1
                continue

            text = row["raw_text"].strip()
            if not text:
                stats.skipped_empty += 1
                continue
            c_hash = content_hash(text)
            if c_hash in seen_hashes:
                stats.skipped_hash_duplicate += 1
                continue
            classification = classify_research_content(text)
            symbols = extract_symbols(text)
            stats.current_class = classification.content_type.value
            stats.content_type_counts[stats.current_class] += 1
            has_sym, has_dir, has_levels = count_extraction_coverage(
                text, stats.current_class,
            )
            if has_sym:
                stats.symbols_extracted += 1
            if has_dir:
                stats.directions_extracted += 1
            if has_levels:
                stats.levels_extracted += 1
            channel_inserted[ch] += 1
            batch_rows.append({
                **row,
                "content_hash": c_hash,
                "content_type": classification.content_type.value,
                "symbols_json": symbols,
                "deterministic_confidence": classification.confidence,
            })
            batch_hashes.add(c_hash)
            seen_hashes.add(c_hash)

            if len(batch_rows) >= 500:
                stats, channel_inserted = _flush_batch(
                    conn, batch_rows, batch_hashes, stats, channel_inserted,
                )
                batch_rows = []
                batch_hashes = set()

            if progress_every and stats.scanned % progress_every == 0:
                elapsed = max(time.monotonic() - t0, 0.001)
                rate = stats.scanned / elapsed
                total = stats.total_estimate
                eta_s = (total - stats.scanned) / rate if total and rate > 0 else 0
                total_s = f"{total:,}" if total else "?"
                eta_s_str = f"{int(eta_s)}s" if eta_s > 0 else "?"
                print(
                    f"Processed: {stats.scanned:,} / {total_s}\n"
                    f"Inserted posts: {stats.inserted:,}\n"
                    f"Duplicates skipped: {stats.skipped_duplicate:,}\n"
                    f"Content-hash duplicates: {stats.skipped_hash_duplicate:,}\n"
                    f"Current class: {stats.current_class}\n"
                    f"Speed: {rate:.0f} msg/s\n"
                    f"ETA: {eta_s_str}",
                    file=sys.stderr,
                    flush=True,
                )

        if batch_rows:
            stats, channel_inserted = _flush_batch(
                conn, batch_rows, batch_hashes, stats, channel_inserted,
            )
    finally:
        reader.close()
    stats.per_channel = dict(channel_inserted)
    return stats


def _flush_batch(
    conn: Any,
    rows: list[dict[str, Any]],
    hashes: set[str],
    stats: IngestResearchStats,
    channel_inserted: dict[str, int],
) -> tuple[IngestResearchStats, dict[str, int]]:
    existing = _existing_hashes(conn, hashes)
    for row in rows:
        if row["content_hash"] in existing:
            stats.skipped_hash_duplicate += 1
            continue
        post_id = _insert_post(
            conn,
            source_message_id=row["source_message_id"],
            channel_name=row["channel_name"],
            message_ts=row["message_ts"],
            raw_text=row["raw_text"],
            c_hash=row["content_hash"],
            content_type=row["content_type"],
            symbols=row["symbols_json"],
            confidence=row["deterministic_confidence"],
        )
        if post_id:
            stats.inserted += 1
        else:
            stats.skipped_duplicate += 1
            channel_inserted[row["channel_name"]] = max(
                0, channel_inserted[row["channel_name"]] - 1,
            )
    conn.commit()
    return stats, channel_inserted


def run_thesis_extract(
    conn: Any,
    *,
    channel: str | None = None,
    limit: int | None = None,
    chunk_size: int = 500,
    progress_every: int = 250,
) -> "ThesisExtractStats":
    """Extract and persist theses for posts without existing theses."""
    import sys

    from bot.research.futures_agent.research_reconciliation import ThesisExtractStats
    from bot.research.futures_agent.thesis_extract import extract_theses_from_post

    validate_write_table("futures_agent_trader_theses")
    stats = ThesisExtractStats()
    t0 = time.monotonic()

    ch_clause = ""
    count_params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        count_params.append(channel)
    stats.posts_total = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE t.id IS NULL{ch_clause}
        """,
        count_params,
    ).fetchone()["n"]

    last_id = 0
    while True:
        q = """
            SELECT p.id, p.raw_text, p.content_type, p.symbols_json
            FROM futures_agent_trader_posts p
            LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
            WHERE t.id IS NULL AND p.id > ?
        """
        params: list[Any] = [last_id]
        if channel:
            q += " AND p.channel_name = ?"
            params.append(channel)
        q += " ORDER BY p.id ASC LIMIT ?"
        params.append(chunk_size)
        rows = conn.execute(q, params).fetchall()
        if not rows:
            break
        for row in rows:
            stats.posts_scanned += 1
            last_id = row["id"]
            symbols = json.loads(row["symbols_json"] or "[]")
            theses = extract_theses_from_post(
                row["raw_text"],
                row["content_type"],
                symbols=symbols,
            )
            thesis_created = False
            for thesis in theses:
                if thesis.unresolved:
                    stats.unresolved_skipped += 1
                    continue
                thesis_id = insert_returning_id(
                    conn,
                    """
                    INSERT INTO futures_agent_trader_theses (
                        post_id, symbol, direction, thesis_text, horizon,
                        condition_text, invalidation_text, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"], thesis.symbol, thesis.direction, thesis.thesis_text,
                        thesis.horizon, thesis.condition_text, thesis.invalidation_text,
                        thesis.confidence,
                    ),
                )
                stats.theses_inserted += 1
                thesis_created = True
                for level in thesis.levels:
                    conn.execute(
                        """
                        INSERT INTO futures_agent_trader_levels (
                            thesis_id, level_type, price, ordinal, confidence
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            thesis_id, level.level_type, level.price,
                            level.ordinal, level.confidence,
                        ),
                    )
                    stats.levels_inserted += 1
            if not thesis_created:
                stats.posts_without_thesis += 1
            if limit and stats.posts_scanned >= limit:
                conn.commit()
                return stats
        conn.commit()

        if progress_every and stats.posts_scanned % progress_every == 0:
            elapsed = max(time.monotonic() - t0, 0.001)
            rate = stats.posts_scanned / elapsed
            total = stats.posts_total
            eta_s = (total - stats.posts_scanned) / rate if total and rate > 0 else 0
            eta_s_str = f"{int(eta_s)}s" if eta_s > 0 else "?"
            print(
                f"Processed posts: {stats.posts_scanned:,} / {total:,}\n"
                f"Theses created: {stats.theses_inserted:,}\n"
                f"Posts without thesis: {stats.posts_without_thesis:,}\n"
                f"Levels created: {stats.levels_inserted:,}\n"
                f"Speed: {rate:.0f} posts/s\n"
                f"ETA: {eta_s_str}",
                file=sys.stderr,
                flush=True,
            )

        if len(rows) < chunk_size:
            break

    # Re-count remaining posts without thesis
    stats.posts_without_thesis = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE t.id IS NULL{ch_clause}
        """,
        count_params,
    ).fetchone()["n"]
    return stats


def thesis_extract_stats_dict(stats: Any) -> dict[str, int]:
    return {
        "posts_scanned": stats.posts_scanned,
        "posts_total": stats.posts_total,
        "theses_inserted": stats.theses_inserted,
        "levels_inserted": stats.levels_inserted,
        "unresolved_skipped": stats.unresolved_skipped,
        "posts_without_thesis": stats.posts_without_thesis,
    }
