"""Phase E.5 — daily research digest."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.alert_config import alert_locale


def _t(key: str, **kwargs) -> str:
    loc = alert_locale()
    table = _STRINGS.get(loc, _STRINGS["en"])
    text = table.get(key, _STRINGS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def _day_bounds(now: int | None = None) -> tuple[int, int, str]:
    now = now or int(time.time())
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    start = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return start, now, dt.strftime("%Y-%m-%d")


def build_daily_digest(conn: Any, *, now: int | None = None) -> str:
    start, end, day_key = _day_bounds(now)

    crypto = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events
        WHERE event_ts >= ? AND event_ts < ? AND (asset_class IS NULL OR asset_class = 'CRYPTO')
        """,
        (start, end),
    ).fetchone()
    tradfi = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events
        WHERE event_ts >= ? AND event_ts < ? AND asset_class IS NOT NULL AND asset_class != 'CRYPTO'
        """,
        (start, end),
    ).fetchone()
    reversals = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_pending_shocks
        WHERE confirmed_reversal IS NOT NULL AND confirmed_reversal != '' AND updated_at >= ?
        """,
        (start,),
    ).fetchone()
    paper = conn.execute(
        "SELECT COUNT(*) AS n FROM paper_strategy_runs WHERE entry_ts >= ?",
        (start,),
    ).fetchone()
    wins = conn.execute(
        """
        SELECT COUNT(*) AS n FROM paper_strategy_runs
        WHERE exit_ts >= ? AND net_return > 0
        """,
        (start,),
    ).fetchone()
    closed = conn.execute(
        "SELECT COUNT(*) AS n FROM paper_strategy_runs WHERE exit_ts >= ?",
        (start,),
    ).fetchone()

    wr = 0
    if closed and int(closed["n"]) > 0:
        wr = int(100 * int(wins["n"]) / int(closed["n"]))

    best = conn.execute(
        """
        SELECT e.symbol, AVG(r.net_return) AS avg_ret
        FROM paper_strategy_runs r JOIN market_events e ON e.id = r.event_id
        WHERE r.exit_ts >= ? GROUP BY e.symbol ORDER BY avg_ret DESC LIMIT 1
        """,
        (start,),
    ).fetchone()
    worst = conn.execute(
        """
        SELECT e.symbol, AVG(r.net_return) AS avg_ret
        FROM paper_strategy_runs r JOIN market_events e ON e.id = r.event_id
        WHERE r.exit_ts >= ? GROUP BY e.symbol ORDER BY avg_ret ASC LIMIT 1
        """,
        (start,),
    ).fetchone()

    ai_rows = conn.execute(
        """
        SELECT movement_interpretation, COUNT(*) AS n
        FROM market_event_ai_analyses WHERE created_at >= ?
        GROUP BY movement_interpretation ORDER BY n DESC LIMIT 3
        """,
        (start,),
    ).fetchall()
    ai_obs = ", ".join(f"{r['movement_interpretation']}({r['n']})" for r in ai_rows) or "—"

    news_row = conn.execute(
        """
        SELECT context_json FROM market_event_context
        WHERE context_type = 'NEWS' AND context_ts >= ?
        ORDER BY context_ts DESC LIMIT 1
        """,
        (start,),
    ).fetchone()
    top_news = "—"
    if news_row:
        top_news = "News context linked today"

    lines = [
        _t("title"),
        "",
        _t("crypto_shocks"),
        str(int(crypto["n"] if crypto else 0)),
        "",
        _t("tradfi_shocks"),
        str(int(tradfi["n"] if tradfi else 0)),
        "",
        _t("reversal_confirmed"),
        str(int(reversals["n"] if reversals else 0)),
        "",
        _t("paper_trades"),
        str(int(paper["n"] if paper else 0)),
        "",
        _t("win_rate"),
        f"{wr}%",
        "",
        _t("best_asset"),
        best["symbol"] if best else "—",
        "",
        _t("worst_asset"),
        worst["symbol"] if worst else "—",
        "",
        _t("top_news"),
        top_news,
        "",
        _t("ai_observations"),
        ai_obs,
    ]
    return "\n".join(lines), day_key


def should_send_daily(conn: Any, *, now: int | None = None) -> bool:
    from bot.research.market_events.alert_engine.config import DAILY_DIGEST_HOUR_UTC, daily_digest_enabled
    if not daily_digest_enabled():
        return False
    now = now or int(time.time())
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    if dt.hour < DAILY_DIGEST_HOUR_UTC:
        return False
    _, _, day_key = _day_bounds(now)
    row = conn.execute(
        "SELECT id FROM market_events_digest_log WHERE digest_type = 'daily' AND period_key = ?",
        (day_key,),
    ).fetchone()
    return row is None


def send_daily_digest(conn: Any, *, now: int | None = None) -> bool:
    msg, day_key = build_daily_digest(conn, now=now)
    from bot.research.market_events.market_event_alerts import _safe_alert
    sent = _safe_alert(
        conn, event_id=0, alert_type="DAILY_DIGEST", detail=day_key,
        message=msg, enabled=True,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO market_events_digest_log (
          digest_type, period_key, message_text, sent, created_at
        ) VALUES ('daily', ?, ?, ?, ?)
        """,
        (day_key, msg, 1 if sent else 0, int(time.time())),
    )
    conn.execute(
        "UPDATE market_events_scheduler_state SET last_daily_digest_ts = ?, updated_at = ? WHERE id = 1",
        (int(time.time()), int(time.time())),
    )
    return sent


_STRINGS = {
    "en": {
        "title": "📊 DAILY REPORT",
        "crypto_shocks": "Crypto shocks",
        "tradfi_shocks": "TradFi shocks",
        "reversal_confirmed": "Reversal confirmed",
        "paper_trades": "Paper trades",
        "win_rate": "Win rate",
        "best_asset": "Best asset",
        "worst_asset": "Worst asset",
        "top_news": "Top news",
        "ai_observations": "AI observations",
    },
    "ru": {
        "title": "📊 DAILY REPORT",
        "crypto_shocks": "Crypto shocks",
        "tradfi_shocks": "TradFi shocks",
        "reversal_confirmed": "Reversal confirmed",
        "paper_trades": "Paper trades",
        "win_rate": "Win rate",
        "best_asset": "Best asset",
        "worst_asset": "Worst asset",
        "top_news": "Top news",
        "ai_observations": "AI observations",
    },
}
