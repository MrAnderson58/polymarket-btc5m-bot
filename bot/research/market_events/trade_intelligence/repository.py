"""Trade Intelligence V1 persistence — read/write knowledge envelope pieces."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.trade_intelligence.models import (
    AISummary,
    ContextItem,
    MarketSnapshot,
    NewsItem,
    Note,
    Outcome,
    Tag,
    TelegramItem,
    TradeRecord,
)
from bot.research.market_events.trade_intelligence.schema import ensure_trade_intelligence_schema


def _dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, default=str)


def _loads(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _row_trade(row: Any) -> TradeRecord:
    return TradeRecord(
        id=int(row["id"]),
        source=str(row["source"]),
        external_id=row["external_id"],
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        entry_ts=int(row["entry_ts"]) if row["entry_ts"] is not None else None,
        exit_ts=int(row["exit_ts"]) if row["exit_ts"] is not None else None,
        entry_price=float(row["entry_price"]) if row["entry_price"] is not None else None,
        exit_price=float(row["exit_price"]) if row["exit_price"] is not None else None,
        size=float(row["size"]) if row["size"] is not None else None,
        pnl_usd=float(row["pnl_usd"]) if row["pnl_usd"] is not None else None,
        pnl_pct=float(row["pnl_pct"]) if row["pnl_pct"] is not None else None,
        strategy=row["strategy"],
        status=str(row["status"] or "closed"),
        raw_json=_loads(row["raw_json"]),
        created_at=int(row["created_at"]) if row["created_at"] is not None else None,
        updated_at=int(row["updated_at"]) if row["updated_at"] is not None else None,
    )


class TradeRepository:
    def __init__(self, conn: Any) -> None:
        self.conn = conn
        ensure_trade_intelligence_schema(conn)

    def upsert_trade(self, trade: TradeRecord) -> int:
        now = int(time.time())
        if trade.external_id:
            existing = self.conn.execute(
                "SELECT id FROM ti_trades WHERE source = ? AND external_id = ?",
                (trade.source, trade.external_id),
            ).fetchone()
            if existing:
                tid = int(existing["id"])
                self.conn.execute(
                    """
                    UPDATE ti_trades SET
                      symbol=?, side=?, entry_ts=?, exit_ts=?, entry_price=?, exit_price=?,
                      size=?, pnl_usd=?, pnl_pct=?, strategy=?, status=?, raw_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        trade.symbol, trade.side, trade.entry_ts, trade.exit_ts,
                        trade.entry_price, trade.exit_price, trade.size, trade.pnl_usd,
                        trade.pnl_pct, trade.strategy, trade.status, _dumps(trade.raw_json),
                        now, tid,
                    ),
                )
                return tid

        cur = self.conn.execute(
            """
            INSERT INTO ti_trades (
              source, external_id, symbol, side, entry_ts, exit_ts, entry_price, exit_price,
              size, pnl_usd, pnl_pct, strategy, status, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.source, trade.external_id, trade.symbol, trade.side,
                trade.entry_ts, trade.exit_ts, trade.entry_price, trade.exit_price,
                trade.size, trade.pnl_usd, trade.pnl_pct, trade.strategy, trade.status,
                _dumps(trade.raw_json), now, now,
            ),
        )
        return int(cur.lastrowid)

    def get_trade(self, trade_id: int) -> TradeRecord | None:
        row = self.conn.execute("SELECT * FROM ti_trades WHERE id = ?", (trade_id,)).fetchone()
        return _row_trade(row) if row else None

    def list_trades(
        self,
        *,
        source: str | None = None,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[TradeRecord]:
        sql = "SELECT * FROM ti_trades WHERE 1=1"
        params: list[Any] = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        if symbol:
            sql += " AND UPPER(symbol) = ?"
            params.append(symbol.upper())
        sql += " ORDER BY COALESCE(exit_ts, entry_ts, created_at) DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [_row_trade(r) for r in self.conn.execute(sql, params).fetchall()]

    def add_market_snapshot(self, snap: MarketSnapshot) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            """
            INSERT INTO ti_market_snapshots (
              trade_id, snapshot_ts, price, funding, oi, volatility, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.trade_id, snap.snapshot_ts, snap.price, snap.funding, snap.oi,
                snap.volatility, _dumps(snap.payload), now,
            ),
        )
        return int(cur.lastrowid)

    def add_news(self, item: NewsItem) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            """
            INSERT INTO ti_news (
              trade_id, news_ts, title, summary, source, url, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.trade_id, item.news_ts, item.title, item.summary, item.source,
                item.url, _dumps(item.payload), now,
            ),
        )
        return int(cur.lastrowid)

    def add_telegram(self, item: TelegramItem) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            """
            INSERT INTO ti_telegram (
              trade_id, message_ts, channel, message_id, text, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.trade_id, item.message_ts, item.channel, item.message_id,
                item.text, _dumps(item.payload), now,
            ),
        )
        return int(cur.lastrowid)

    def add_context(self, item: ContextItem) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            """
            INSERT INTO ti_context (
              trade_id, context_key, context_value, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (item.trade_id, item.context_key, item.context_value, _dumps(item.payload), now),
        )
        return int(cur.lastrowid)

    def upsert_outcome(self, outcome: Outcome) -> int:
        now = int(time.time())
        existing = self.conn.execute(
            "SELECT id FROM ti_outcomes WHERE trade_id = ?",
            (outcome.trade_id,),
        ).fetchone()
        if existing:
            oid = int(existing["id"])
            self.conn.execute(
                """
                UPDATE ti_outcomes SET
                  outcome_ts=?, result=?, pnl_usd=?, pnl_pct=?, exit_reason=?, payload_json=?
                WHERE id=?
                """,
                (
                    outcome.outcome_ts, outcome.result, outcome.pnl_usd, outcome.pnl_pct,
                    outcome.exit_reason, _dumps(outcome.payload), oid,
                ),
            )
            return oid
        cur = self.conn.execute(
            """
            INSERT INTO ti_outcomes (
              trade_id, outcome_ts, result, pnl_usd, pnl_pct, exit_reason, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.trade_id, outcome.outcome_ts, outcome.result, outcome.pnl_usd,
                outcome.pnl_pct, outcome.exit_reason, _dumps(outcome.payload), now,
            ),
        )
        return int(cur.lastrowid)

    def add_ai_summary(self, summary: AISummary) -> int:
        """Store placeholder / future LLM summary — V1 never generates text."""
        now = int(time.time())
        cur = self.conn.execute(
            """
            INSERT INTO ti_ai_summaries (
              trade_id, model, summary_text, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                summary.trade_id, summary.model, summary.summary_text,
                _dumps(summary.payload), now,
            ),
        )
        return int(cur.lastrowid)

    def add_tag(self, trade_id: int, tag: str) -> None:
        now = int(time.time())
        self.conn.execute(
            """
            INSERT OR IGNORE INTO ti_tags (trade_id, tag, created_at) VALUES (?, ?, ?)
            """,
            (trade_id, tag.strip().lower(), now),
        )

    def add_note(self, note: Note) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            """
            INSERT INTO ti_notes (trade_id, note_ts, author, text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (note.trade_id, note.note_ts or now, note.author, note.text, now),
        )
        return int(cur.lastrowid)

    def load_snapshots(self, trade_id: int) -> list[MarketSnapshot]:
        rows = self.conn.execute(
            "SELECT * FROM ti_market_snapshots WHERE trade_id = ? ORDER BY snapshot_ts",
            (trade_id,),
        ).fetchall()
        return [
            MarketSnapshot(
                id=int(r["id"]), trade_id=trade_id,
                snapshot_ts=r["snapshot_ts"], price=r["price"], funding=r["funding"],
                oi=r["oi"], volatility=r["volatility"], payload=_loads(r["payload_json"]),
            )
            for r in rows
        ]

    def load_news(self, trade_id: int) -> list[NewsItem]:
        rows = self.conn.execute(
            "SELECT * FROM ti_news WHERE trade_id = ? ORDER BY news_ts",
            (trade_id,),
        ).fetchall()
        return [
            NewsItem(
                id=int(r["id"]), trade_id=trade_id, news_ts=r["news_ts"],
                title=r["title"] or "", summary=r["summary"] or "",
                source=r["source"], url=r["url"], payload=_loads(r["payload_json"]),
            )
            for r in rows
        ]

    def load_telegram(self, trade_id: int) -> list[TelegramItem]:
        rows = self.conn.execute(
            "SELECT * FROM ti_telegram WHERE trade_id = ? ORDER BY message_ts",
            (trade_id,),
        ).fetchall()
        return [
            TelegramItem(
                id=int(r["id"]), trade_id=trade_id, message_ts=r["message_ts"],
                channel=r["channel"], message_id=r["message_id"],
                text=r["text"] or "", payload=_loads(r["payload_json"]),
            )
            for r in rows
        ]

    def load_context(self, trade_id: int) -> list[ContextItem]:
        rows = self.conn.execute(
            "SELECT * FROM ti_context WHERE trade_id = ? ORDER BY id",
            (trade_id,),
        ).fetchall()
        return [
            ContextItem(
                id=int(r["id"]), trade_id=trade_id,
                context_key=r["context_key"], context_value=r["context_value"] or "",
                payload=_loads(r["payload_json"]),
            )
            for r in rows
        ]

    def load_outcome(self, trade_id: int) -> Outcome | None:
        r = self.conn.execute(
            "SELECT * FROM ti_outcomes WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        if not r:
            return None
        return Outcome(
            id=int(r["id"]), trade_id=trade_id, outcome_ts=r["outcome_ts"],
            result=r["result"], pnl_usd=r["pnl_usd"], pnl_pct=r["pnl_pct"],
            exit_reason=r["exit_reason"], payload=_loads(r["payload_json"]),
        )

    def load_ai_summary(self, trade_id: int) -> AISummary | None:
        r = self.conn.execute(
            """
            SELECT * FROM ti_ai_summaries WHERE trade_id = ?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
        if not r:
            return None
        return AISummary(
            id=int(r["id"]), trade_id=trade_id, model=r["model"],
            summary_text=r["summary_text"] or "", created_at=r["created_at"],
            payload=_loads(r["payload_json"]),
        )

    def load_tags(self, trade_id: int) -> list[Tag]:
        rows = self.conn.execute(
            "SELECT * FROM ti_tags WHERE trade_id = ? ORDER BY tag",
            (trade_id,),
        ).fetchall()
        return [Tag(id=int(r["id"]), trade_id=trade_id, tag=r["tag"]) for r in rows]

    def load_notes(self, trade_id: int) -> list[Note]:
        rows = self.conn.execute(
            "SELECT * FROM ti_notes WHERE trade_id = ? ORDER BY note_ts DESC, id DESC",
            (trade_id,),
        ).fetchall()
        return [
            Note(
                id=int(r["id"]), trade_id=trade_id, note_ts=r["note_ts"],
                author=r["author"], text=r["text"] or "",
            )
            for r in rows
        ]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in (
            "ti_trades", "ti_market_snapshots", "ti_news", "ti_telegram",
            "ti_context", "ti_outcomes", "ti_ai_summaries", "ti_tags", "ti_notes",
        ):
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            out[table] = int(row["n"] or 0)
        return out
