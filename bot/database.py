import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR, DATABASE_PATH


def _schema_path() -> Path:
    return BASE_DIR / "schema.sql"


_MIGRATIONS = (
    "ALTER TABLE virtual_trades ADD COLUMN market_end_price REAL",
    "ALTER TABLE virtual_trades ADD COLUMN distance_at_close REAL",
    "ALTER TABLE virtual_trades ADD COLUMN min_delta_after_entry REAL",
    "ALTER TABLE virtual_trades ADD COLUMN max_delta_after_entry REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN last_bid REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN exit_price REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN pnl_percent REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN pnl_usdc REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN holding_time_seconds REAL",
)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    virtual_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(virtual_trades)").fetchall()
    }
    early_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(early_reversion_trades)").fetchall()
    }
    for sql in _MIGRATIONS:
        parts = sql.split()
        column = parts[parts.index("COLUMN") + 1]
        table = parts[2]
        existing = virtual_cols if table == "virtual_trades" else early_cols
        if column not in existing:
            conn.execute(sql)
            existing.add(column)


def backfill_early_reversion_trades(conn: sqlite3.Connection) -> int:
    """Recalculate settlement fields for closed trades missing exit data."""
    from bot.config import EARLY_REVERSION_POSITION_SIZE_USDC

    rows = conn.execute(
        """
        SELECT id, entry_price, target_price, entry_ts, end_ts,
               reached_target, last_bid, time_to_target_seconds,
               exit_price, pnl_percent, pnl_usdc, holding_time_seconds
        FROM early_reversion_trades
        WHERE status = 'closed'
          AND (
            exit_price IS NULL
            OR pnl_percent IS NULL
            OR pnl_usdc IS NULL
            OR holding_time_seconds IS NULL
          )
        ORDER BY id ASC
        """
    ).fetchall()

    updated = 0
    for row in rows:
        entry_price = float(row["entry_price"])
        reached_target = bool(row["reached_target"])

        if reached_target:
            exit_price = float(row["target_price"])
            holding_time = row["time_to_target_seconds"]
            if holding_time is None:
                holding_time = float(max(row["end_ts"] - row["entry_ts"], 0))
        elif row["last_bid"] is not None:
            exit_price = float(row["last_bid"])
            holding_time = float(max(row["end_ts"] - row["entry_ts"], 0))
        else:
            exit_price = entry_price
            holding_time = float(max(row["end_ts"] - row["entry_ts"], 0))

        pnl_percent = (exit_price - entry_price) / entry_price * 100
        shares = EARLY_REVERSION_POSITION_SIZE_USDC / entry_price
        pnl_usdc = shares * (exit_price - entry_price)

        conn.execute(
            """
            UPDATE early_reversion_trades
            SET exit_price = ?,
                pnl_percent = ?,
                pnl_usdc = ?,
                holding_time_seconds = ?
            WHERE id = ?
            """,
            (exit_price, pnl_percent, pnl_usdc, holding_time, row["id"]),
        )
        updated += 1

    return updated


def init_db(db_path: Path | None = None) -> None:
    path = db_path or DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = _schema_path().read_text(encoding="utf-8")
    with connect(path) as conn:
        conn.executescript(schema)
        _apply_migrations(conn)
        conn.commit()


@contextmanager
def connect(db_path: Path | None = None):
    path = db_path or DATABASE_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def insert_market_check(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    seconds_remaining: float,
    strike_price: float,
    btc_price: float,
    yes_bid: float | None,
    yes_ask: float | None,
    no_bid: float | None,
    no_ask: float | None,
    signal: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO market_checks (
            market_slug, seconds_remaining, strike_price, btc_price,
            yes_bid, yes_ask, no_bid, no_ask, signal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            seconds_remaining,
            strike_price,
            btc_price,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            signal,
        ),
    )


def has_open_trade_for_market(conn: sqlite3.Connection, market_slug: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM virtual_trades WHERE market_slug = ? LIMIT 1",
        (market_slug,),
    ).fetchone()
    return row is not None


def insert_virtual_trade(
    conn: sqlite3.Connection,
    trade: dict[str, Any],
) -> int:
    entry_delta = trade["btc_delta_at_entry"]
    side = trade["side"]
    min_delta = entry_delta if side == "YES" else None
    max_delta = entry_delta if side == "NO" else None

    cursor = conn.execute(
        """
        INSERT INTO virtual_trades (
            market_slug, condition_id, window_start_ts, end_ts, strike_price,
            side, token_id, entry_ask, entry_bid, btc_price_at_entry,
            btc_delta_at_entry, min_delta_after_entry, max_delta_after_entry,
            seconds_remaining, size_usdc, shares
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade["market_slug"],
            trade.get("condition_id"),
            trade["window_start_ts"],
            trade["end_ts"],
            trade["strike_price"],
            side,
            trade["token_id"],
            trade["entry_ask"],
            trade.get("entry_bid"),
            trade["btc_price_at_entry"],
            entry_delta,
            min_delta,
            max_delta,
            trade["seconds_remaining"],
            trade["size_usdc"],
            trade["shares"],
        ),
    )
    return int(cursor.lastrowid)


def get_open_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM virtual_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC
        """
    ).fetchall()


def get_open_trades_due_for_settlement(
    conn: sqlite3.Connection,
    now_ts: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM virtual_trades
        WHERE status = 'open' AND end_ts <= ?
        ORDER BY end_ts ASC
        """,
        (now_ts,),
    ).fetchall()


def settle_virtual_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    outcome: str,
    market_end_price: float,
    strike_price: float,
    payout_usdc: float,
    pnl_usdc: float,
) -> None:
    distance_at_close = abs(market_end_price - strike_price)
    conn.execute(
        """
        UPDATE virtual_trades
        SET status = 'settled',
            outcome = ?,
            settlement_btc_price = ?,
            market_end_price = ?,
            distance_at_close = ?,
            payout_usdc = ?,
            pnl_usdc = ?,
            settled_at = datetime('now')
        WHERE id = ?
        """,
        (
            outcome,
            market_end_price,
            market_end_price,
            distance_at_close,
            payout_usdc,
            pnl_usdc,
            trade_id,
        ),
    )


def upsert_early_reversion_market_prices(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    min_yes_price: float | None,
    max_yes_price: float | None,
    min_no_price: float | None,
    max_no_price: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO early_reversion_markets (
            market_slug, window_start_ts,
            min_yes_price, max_yes_price, min_no_price, max_no_price
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_slug) DO UPDATE SET
            min_yes_price = excluded.min_yes_price,
            max_yes_price = excluded.max_yes_price,
            min_no_price = excluded.min_no_price,
            max_no_price = excluded.max_no_price,
            updated_at = datetime('now')
        """,
        (
            market_slug,
            window_start_ts,
            min_yes_price,
            max_yes_price,
            min_no_price,
            max_no_price,
        ),
    )


def get_early_reversion_market(
    conn: sqlite3.Connection,
    market_slug: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM early_reversion_markets WHERE market_slug = ?",
        (market_slug,),
    ).fetchone()


def has_early_reversion_trade(
    conn: sqlite3.Connection,
    market_slug: str,
    strategy_name: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM early_reversion_trades
        WHERE market_slug = ? AND strategy_name = ?
        LIMIT 1
        """,
        (market_slug, strategy_name),
    ).fetchone()
    return row is not None


def insert_early_reversion_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    strategy_name: str,
    entry_price: float,
    target_price: float,
    entry_ts: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO early_reversion_trades (
            market_slug, window_start_ts, end_ts, side, strategy_name,
            entry_price, target_price, entry_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            strategy_name,
            entry_price,
            target_price,
            entry_ts,
        ),
    )
    return int(cursor.lastrowid)


def get_open_early_reversion_trades(
    conn: sqlite3.Connection,
    market_slug: str | None = None,
) -> list[sqlite3.Row]:
    if market_slug:
        return conn.execute(
            """
            SELECT * FROM early_reversion_trades
            WHERE status = 'open' AND market_slug = ?
            ORDER BY entry_ts ASC
            """,
            (market_slug,),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM early_reversion_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC, entry_ts ASC
        """
    ).fetchall()


def close_early_reversion_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    reached_target: bool,
    max_profit_percent: float,
    max_drawdown_percent: float,
    time_to_target_seconds: float | None,
    exit_price: float,
    pnl_percent: float,
    pnl_usdc: float,
    holding_time_seconds: float,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_trades
        SET status = 'closed',
            reached_target = ?,
            max_profit_percent = ?,
            max_drawdown_percent = ?,
            time_to_target_seconds = ?,
            exit_price = ?,
            pnl_percent = ?,
            pnl_usdc = ?,
            holding_time_seconds = ?,
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            int(reached_target),
            max_profit_percent,
            max_drawdown_percent,
            time_to_target_seconds,
            exit_price,
            pnl_percent,
            pnl_usdc,
            holding_time_seconds,
            trade_id,
        ),
    )


def has_early_reversion_v2_trade(
    conn: sqlite3.Connection,
    market_slug: str,
    strategy_name: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM early_reversion_v2_trades
        WHERE market_slug = ? AND strategy_name = ?
        LIMIT 1
        """,
        (market_slug, strategy_name),
    ).fetchone()
    return row is not None


def insert_early_reversion_v2_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    strategy_name: str,
    entry_price: float,
    entry_ts: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO early_reversion_v2_trades (
            market_slug, window_start_ts, end_ts, side, strategy_name,
            entry_price, entry_ts, max_price_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            strategy_name,
            entry_price,
            entry_ts,
            entry_price,
        ),
    )
    return int(cursor.lastrowid)


def get_open_early_reversion_v2_trades(
    conn: sqlite3.Connection,
    market_slug: str | None = None,
) -> list[sqlite3.Row]:
    if market_slug:
        return conn.execute(
            """
            SELECT * FROM early_reversion_v2_trades
            WHERE status = 'open' AND market_slug = ?
            ORDER BY entry_ts ASC
            """,
            (market_slug,),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM early_reversion_v2_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC, entry_ts ASC
        """
    ).fetchall()


def update_early_reversion_v2_trade_tracking(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    max_price_seen: float,
    last_bid: float,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v2_trades
        SET max_price_seen = ?, last_bid = ?
        WHERE id = ?
        """,
        (max_price_seen, last_bid, trade_id),
    )


def close_early_reversion_v2_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price: float,
    exit_reason: str,
    pnl_percent: float,
    pnl_usdc: float,
    holding_time_seconds: float,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v2_trades
        SET status = 'closed',
            exit_price = ?,
            exit_reason = ?,
            pnl_percent = ?,
            pnl_usdc = ?,
            holding_time_seconds = ?,
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            exit_price,
            exit_reason,
            pnl_percent,
            pnl_usdc,
            holding_time_seconds,
            trade_id,
        ),
    )
