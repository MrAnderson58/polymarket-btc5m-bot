"""Phase E.1 reporting."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any


def _days_ago_ts(days: int) -> int:
    return int(datetime.now(tz=timezone.utc).timestamp()) - days * 86400


def shock_event_report(conn: Any, *, days: int = 7) -> str:
    since = _days_ago_ts(days)
    events = conn.execute(
        "SELECT * FROM market_events WHERE event_ts >= ? ORDER BY event_ts DESC",
        (since,),
    ).fetchall()
    lines = [
        "SHOCK EVENT REPORT",
        f"window_days: {days}",
        f"events: {len(events)}",
        "",
    ]
    by_sym: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for e in events:
        by_sym[e["symbol"]] = by_sym.get(e["symbol"], 0) + 1
        by_class[e["classification"]] = by_class.get(e["classification"], 0) + 1
    lines.append("By symbol:")
    for k, v in sorted(by_sym.items(), key=lambda x: -x[1]):
        lines.append(f"  {k}: {v}")
    lines.append("By classification:")
    for k, v in sorted(by_class.items(), key=lambda x: -x[1]):
        lines.append(f"  {k}: {v}")
    lines.extend(["", "Recent events:"])
    for e in events[:30]:
        ts = datetime.fromtimestamp(e["event_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"  id={e['id']} {ts} {e['symbol']} {e['direction']} "
            f"ret={e['return_pct']:.2f}% class={e['classification']}",
        )
    lines.extend(["", "=== STRATIFIED (E.2) ==="])
    for col in ("asset_class", "session_regime", "cross_classification"):
        try:
            rows2 = conn.execute(
                f"""
                SELECT {col} AS k, COUNT(*) AS n
                FROM market_events WHERE event_ts >= ? AND {col} IS NOT NULL
                GROUP BY {col} ORDER BY n DESC
                """,
                (since,),
            ).fetchall()
            if rows2:
                lines.append(f"  by {col}:")
                for r in rows2:
                    lines.append(f"    {r['k']}: {r['n']}")
        except Exception:
            pass
    return "\n".join(lines)


def shock_strategy_report(conn: Any, *, strategy_prefix: str | None = None, days: int = 7) -> str:
    since = _days_ago_ts(days)
    q = """
    SELECT r.* FROM paper_strategy_runs r
    JOIN market_events e ON e.id = r.event_id
    WHERE e.event_ts >= ? AND r.exit_ts IS NOT NULL
    """
    params: list[Any] = [since]
    if strategy_prefix:
        q += " AND r.strategy_name LIKE ?"
        params.append(f"{strategy_prefix}%")
    rows = conn.execute(q, params).fetchall()
    rets = [float(r["net_return"]) for r in rows if r["net_return"] is not None]
    lines = [
        "SHOCK STRATEGY REPORT",
        f"closed_runs: {len(rows)}",
        f"strategy_filter: {strategy_prefix or 'all'}",
        "",
    ]
    if not rets:
        lines.append("No closed paper runs yet.")
        return "\n".join(lines)
    wins = [r for r in rets if r > 0]
    sorted_rets = sorted(rets)
    lines.extend([
        f"win_rate: {len(wins) / len(rets):.1%}",
        f"mean_net_return: {statistics.mean(rets):.3f}%",
        f"median_net_return: {statistics.median(rets):.3f}%",
        f"expectancy: {statistics.mean(rets):.3f}%",
    ])
    if len(rets) >= 20:
        k = max(1, len(rets) // 10)
        trimmed = sorted_rets[k:-k]
        lines.append(f"trimmed_mean_10pct: {statistics.mean(trimmed):.3f}%")
    total = sum(rets)
    if abs(total) > 1e-9:
        top = sum(sorted_rets[-max(1, len(rets) // 10):])
        lines.append(f"top_10pct_contribution: {top / total:.1%}")
    be_exits = sum(1 for r in rows if r["be_exit"])
    lines.append(f"be_exit_rate: {be_exits / len(rows):.1%}")
    lines.extend(["", "=== STRATIFIED BY ASSET CLASS (E.2) ==="])
    try:
        strat = conn.execute(
            """
            SELECT e.asset_class, COUNT(*) AS n,
                   AVG(r.net_return) AS mean_ret
            FROM paper_strategy_runs r
            JOIN market_events e ON e.id = r.event_id
            WHERE e.event_ts >= ? AND r.exit_ts IS NOT NULL AND e.asset_class IS NOT NULL
            GROUP BY e.asset_class
            """,
            (since,),
        ).fetchall()
        for s in strat:
            lines.append(f"  {s['asset_class']}: N={s['n']} mean={s['mean_ret']:.3f}%")
    except Exception:
        pass
    return "\n".join(lines)


def shock_context_report(conn: Any, *, days: int = 7) -> str:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT c.context_type, COUNT(*) AS n
        FROM market_event_context c
        JOIN market_events e ON e.id = c.event_id
        WHERE e.event_ts >= ?
        GROUP BY c.context_type
        ORDER BY n DESC
        """,
        (since,),
    ).fetchall()
    events_with = conn.execute(
        """
        SELECT COUNT(DISTINCT e.id) FROM market_events e
        JOIN market_event_context c ON c.event_id = e.id
        WHERE e.event_ts >= ?
        """,
        (since,),
    ).fetchone()["n"]
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE event_ts >= ?",
        (since,),
    ).fetchone()["n"]
    lines = [
        "SHOCK CONTEXT REPORT",
        f"events: {total}",
        f"events_with_context: {events_with}",
        "",
    ]
    for r in rows:
        lines.append(f"  {r['context_type']}: {r['n']}")
    without = total - events_with
    lines.append(f"  (no known context): {without}")
    return "\n".join(lines)
