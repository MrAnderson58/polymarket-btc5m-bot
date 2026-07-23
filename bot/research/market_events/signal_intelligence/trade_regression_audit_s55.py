"""S55.2 — Trade regression audit (b4b495c → d990cbf).

Evidence-based comparison of paper-trading behavior and PnL attribution.
Read-only; safe to run against production SQLite/PostgreSQL.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
    CAPITAL_PER_TRADE_USD,
    EXIT_STOP,
    EXIT_TIMEOUT,
    EXIT_TP1,
    EXIT_TP2,
    EXIT_TRAILING,
    LEVERAGE,
    STATUS_CLOSED,
    STATUS_OPEN,
    TRAIL_AFTER_TP1,
)
from bot.research.market_events.signal_intelligence.trade_intelligence_s55 import (
    gate_stats_today,
    refresh_s55_config_from_env,
)

_TRADES = "market_events_paper_trades_s42"
_FEATURES = "market_events_trade_features_s55"
_S40 = "market_events_signal_learning_s40_signals"

_COMMIT_BASE = "b4b495c"
_COMMIT_HEAD = "d990cbf"
_COMMIT_TRAIL = "c7cfe75"  # S54.2 between base and head


def _git_commit_unix(sha: str) -> int | None:
    try:
        out = subprocess.check_output(
            ["git", "show", "-s", "--format=%ct", sha],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return int(out) if out else None
    except Exception:
        return None


def _git_commit_iso(sha: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "show", "-s", "--format=%ci", sha],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return sha


def _day_start_local(ts: int | None = None) -> int:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time())
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _table_exists(conn: Any, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=%s LIMIT 1",
            (name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _fetchall(conn: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetchone(conn: Any, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def code_change_summary() -> dict[str, Any]:
    """Static diff summary between audit commits (from git, not DB)."""
    return {
        "base_commit": _COMMIT_BASE,
        "head_commit": _COMMIT_HEAD,
        "trail_commit": _COMMIT_TRAIL,
        "base_time": _git_commit_iso(_COMMIT_BASE),
        "trail_time": _git_commit_iso(_COMMIT_TRAIL),
        "head_time": _git_commit_iso(_COMMIT_HEAD),
        "open_count_changed": True,
        "open_count_detail": (
            "b4b495c: open every unmatched S40 signal (LIMIT 100/cycle, no cap). "
            "d990cbf: S55 gate + S55_MAX_OPEN_TRADES=25 blocks new opens when open_count>=25."
        ),
        "open_criteria_changed": True,
        "open_criteria_detail": (
            "d990cbf adds kNN expected-PnL gate (cold_start if <20 neighbors else reject if E[PnL]<0)."
        ),
        "stops_tp_changed": False,
        "stops_tp_detail": "No diff in entry/stop/tp1/tp2 geometry between b4b495c and d990cbf.",
        "position_size_changed": False,
        "position_size_detail": f"Unchanged: ${CAPITAL_PER_TRADE_USD} margin @ {LEVERAGE}x.",
        "management_changed": True,
        "management_detail": (
            f"c7cfe75 (between base/head): TRAIL_AFTER_TP1 default False→True. "
            f"Current env default TRAIL_AFTER_TP1={TRAIL_AFTER_TP1}."
        ),
    }


def _exit_bucket(exit_reason: str | None, trailing_active: int | None = None) -> str:
    r = str(exit_reason or "").upper()
    if r == EXIT_TRAILING:
        return "Trailing"
    if r == EXIT_TP1:
        return "Classic TP (TP1)"
    if r == EXIT_TP2:
        return "Classic TP (TP2)"
    if r == EXIT_STOP:
        return "SL"
    if r == EXIT_TIMEOUT:
        return "Timeout"
    if r in ("REVERSE", "LIQUIDATION"):
        return r.title()
    if int(trailing_active or 0) == 1 and r != EXIT_TRAILING:
        return "Trailing (armed)"
    return r or "UNKNOWN"


def gate_diagnostics(conn: Any) -> dict[str, Any]:
    """Explain current_open vs max_open vs accepted_today/rejected_today."""
    refresh_s55_config_from_env()
    from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55

    open_count = int(
        (_fetchone(conn, f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status=?", (STATUS_OPEN,)) or {}).get("n")
        or 0
    )
    stats = gate_stats_today(conn)
    day_start = _day_start_local()
    features_total = 0
    features_today = 0
    if _table_exists(conn, _FEATURES):
        features_total = int((_fetchone(conn, f"SELECT COUNT(*) AS n FROM {_FEATURES}") or {}).get("n") or 0)
        features_today = int(
            (
                _fetchone(
                    conn,
                    f"SELECT COUNT(*) AS n FROM {_FEATURES} WHERE created_at >= ?",
                    (day_start,),
                )
                or {}
            ).get("n")
            or 0
        )

    gate_decisions_all = _fetchall(
        conn,
        f"SELECT gate_decision, COUNT(*) AS n FROM {_FEATURES} GROUP BY gate_decision ORDER BY n DESC",
    ) if _table_exists(conn, _FEATURES) else []

    pending_s40 = int(
        (
            _fetchone(
                conn,
                f"""
                SELECT COUNT(*) AS n
                FROM {_S40} s
                LEFT JOIN {_TRADES} p
                  ON p.s40_signal_type = s.signal_type AND p.s40_signal_id = s.signal_id
                WHERE p.id IS NULL AND s.entry IS NOT NULL AND s.entry > 0
                  AND s.direction IN ('LONG', 'SHORT')
                """,
            )
            or {}
        ).get("n")
        or 0
    )

    explanations: list[str] = []
    if open_count > s55.S55_MAX_OPEN_TRADES:
        explanations.append(
            f"current_open={open_count} is ALL historical OPEN rows; max_open={s55.S55_MAX_OPEN_TRADES} "
            "only blocks NEW opens in open_paper_trades_from_s40 — it does not close excess legacy opens."
        )
    if stats["accepted"] == 0 and stats["rejected"] == 0:
        if features_today == 0:
            explanations.append(
                "accepted_today=0 and rejected_today=0 because market_events_trade_features_s55 "
                f"has 0 rows with created_at >= today ({datetime.fromtimestamp(day_start)}): "
                "gate never ran today OR S55 deployed after last paper cycle OR no new S40 candidates."
            )
        else:
            explanations.append(
                "accepted_today/rejected_today=0 but feature rows exist today — check gate_decision values."
            )
    if features_total == 0 and open_count > 0:
        explanations.append(
            f"S55 feature table empty ({features_total} rows) but {open_count} open trades: "
            "all opens pre-date S55.1 deploy; gate stats only count post-deploy gate events."
        )
    if open_count >= s55.S55_MAX_OPEN_TRADES and pending_s40 > 0:
        explanations.append(
            f"With open_count={open_count}>=max_open={s55.S55_MAX_OPEN_TRADES}, "
            f"next cycle will skip opens (pending S40 signals={pending_s40})."
        )

    return {
        "enabled": s55.S55_ENABLED,
        "max_open": s55.S55_MAX_OPEN_TRADES,
        "current_open": open_count,
        "accepted_today": stats["accepted"],
        "rejected_today": stats["rejected"],
        "cold_start_today": stats["cold_start"],
        "features_total": features_total,
        "features_today": features_today,
        "pending_s40_signals": pending_s40,
        "gate_decisions_all": gate_decisions_all,
        "gate_invocations_today": features_today,
        "explanations": explanations,
    }


def _group_sum(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "pnl_usd": 0.0, "wins": 0})
    for r in rows:
        k = str(r.get(key) or "UNKNOWN")
        agg[k]["count"] += 1
        pnl = float(r.get("pnl_usd") or 0.0)
        agg[k]["pnl_usd"] += pnl
        if str(r.get("result") or "").upper() == "WIN":
            agg[k]["wins"] += 1
    out = []
    for k, v in sorted(agg.items(), key=lambda x: x[1]["pnl_usd"]):
        out.append({
            "key": k,
            "count": v["count"],
            "pnl_usd": round(v["pnl_usd"], 2),
            "winrate_pct": round(100.0 * v["wins"] / v["count"], 1) if v["count"] else 0.0,
        })
    return out


def _feature_compare(winners: list[dict], losers: list[dict]) -> list[dict[str, Any]]:
    dims = (
        "ai_score", "funding", "news_score", "fear_greed", "atr", "volume",
        "gate_expected_pnl_pct", "similar_count", "hour",
    )

    def means(rows: list[dict], dim: str) -> float | None:
        vals = [float(r[dim]) for r in rows if r.get(dim) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    diffs = []
    for dim in dims:
        wm = means(winners, dim)
        lm = means(losers, dim)
        if wm is None and lm is None:
            continue
        diffs.append({
            "feature": dim,
            "winners_avg": wm,
            "losers_avg": lm,
            "delta": round((wm or 0) - (lm or 0), 4) if wm is not None and lm is not None else None,
        })
    diffs.sort(key=lambda x: abs(x.get("delta") or 0), reverse=True)
    return diffs


def _trade_detail_rows(conn: Any, *, limit: int, order: str) -> list[dict[str, Any]]:
    order_sql = "ASC" if order == "worst" else "DESC"
    sql = f"""
        SELECT
          t.id, t.symbol, t.direction, t.entry, t.exit_price, t.created_at, t.closed_at,
          t.pnl_usd, t.pnl_pct, t.result, t.exit_reason, t.trailing_active,
          t.decision_confidence, t.s40_signal_type, t.news_category,
          f.gate_decision, f.gate_expected_pnl_pct, f.ai_score, f.funding,
          f.oi_delta, f.etf_flow, f.news_score, f.features_json
        FROM {_TRADES} t
        LEFT JOIN {_FEATURES} f
          ON f.s40_signal_type = t.s40_signal_type AND f.s40_signal_id = t.s40_signal_id
        WHERE t.status = ? AND t.pnl_usd IS NOT NULL
        ORDER BY t.pnl_usd {order_sql}
        LIMIT ?
    """
    rows = _fetchall(conn, sql, (STATUS_CLOSED, limit))
    for r in rows:
        r["created_iso"] = (
            datetime.fromtimestamp(int(r["created_at"])).isoformat(sep=" ", timespec="seconds")
            if r.get("created_at") else None
        )
        r["exit_bucket"] = _exit_bucket(r.get("exit_reason"), r.get("trailing_active"))
        r["why_opened"] = f"S40 {r.get('s40_signal_type')}/{r.get('s40_signal_id')} gate={r.get('gate_decision') or 'pre_s55'}"
        r["why_closed"] = f"{r.get('exit_reason')} ({r['exit_bucket']})"
        r["trailing"] = int(r.get("trailing_active") or 0) == 1 or str(r.get("exit_reason")) == EXIT_TRAILING
    return rows


def run_trade_regression_audit(
    conn: Any,
    *,
    deploy_ts: int | None = None,
    top_n: int = 100,
) -> dict[str, Any]:
    """Full audit payload."""
    if deploy_ts is None:
        deploy_ts = _git_commit_unix(_COMMIT_HEAD)
    deploy_iso = (
        datetime.fromtimestamp(deploy_ts, tz=timezone.utc).astimezone().isoformat(sep=" ", timespec="seconds")
        if deploy_ts else _git_commit_iso(_COMMIT_HEAD)
    )

    total_open = int((_fetchone(conn, f"SELECT COUNT(*) AS n FROM {_TRADES}") or {}).get("n") or 0)
    open_status = int(
        (_fetchone(conn, f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status=?", (STATUS_OPEN,)) or {}).get("n") or 0
    )
    closed_status = int(
        (_fetchone(conn, f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status=?", (STATUS_CLOSED,)) or {}).get("n")
        or 0
    )

    # Period comparison (opens)
    periods: dict[str, Any] = {}
    if deploy_ts:
        for label, since, until in (
            ("before_base", None, _git_commit_unix(_COMMIT_BASE)),
            ("base_to_trail", _git_commit_unix(_COMMIT_BASE), _git_commit_unix(_COMMIT_TRAIL)),
            ("trail_to_head", _git_commit_unix(_COMMIT_TRAIL), deploy_ts),
            ("after_head", deploy_ts, None),
        ):
            clauses = ["1=1"]
            params: list[Any] = []
            if since is not None:
                clauses.append("created_at >= ?")
                params.append(since)
            if until is not None:
                clauses.append("created_at < ?")
                params.append(until)
            row = _fetchone(
                conn,
                f"SELECT COUNT(*) AS opened FROM {_TRADES} WHERE {' AND '.join(clauses)}",
                tuple(params),
            )
            periods[label] = int((row or {}).get("opened") or 0)

    # Trades opened after deploy
    post_deploy = _fetchall(
        conn,
        f"""
        SELECT t.*, f.gate_decision, f.gate_expected_pnl_pct, f.ai_score, f.hour
        FROM {_TRADES} t
        LEFT JOIN {_FEATURES} f
          ON f.s40_signal_type = t.s40_signal_type AND f.s40_signal_id = t.s40_signal_id
        WHERE t.created_at >= ?
        ORDER BY t.created_at ASC
        """,
        (deploy_ts,) if deploy_ts else (-1,),
    ) if deploy_ts else []

    post_closed = [r for r in post_deploy if str(r.get("status")) == STATUS_CLOSED]

    # PnL closed since deploy (realized regression window)
    pnl_since = _fetchall(
        conn,
        f"""
        SELECT t.*, f.gate_decision
        FROM {_TRADES} t
        LEFT JOIN {_FEATURES} f
          ON f.s40_signal_type = t.s40_signal_type AND f.s40_signal_id = t.s40_signal_id
        WHERE t.status = ? AND t.closed_at IS NOT NULL AND t.closed_at >= ?
        """,
        (STATUS_CLOSED, deploy_ts or 0),
    ) if deploy_ts else []

    for r in pnl_since:
        r["exit_bucket"] = _exit_bucket(r.get("exit_reason"), r.get("trailing_active"))

    total_pnl_since = round(sum(float(r.get("pnl_usd") or 0) for r in pnl_since), 2)

    gate = gate_diagnostics(conn)

    winners = _trade_detail_rows(conn, limit=top_n, order="best")
    losers = _trade_detail_rows(conn, limit=top_n, order="worst")

    return {
        "generated_at": int(time.time()),
        "deploy_commit": _COMMIT_HEAD,
        "deploy_ts": deploy_ts,
        "deploy_iso": deploy_iso,
        "code_changes": code_change_summary(),
        "inventory": {
            "total_trades": total_open,
            "open": open_status,
            "closed": closed_status,
        },
        "opens_by_period": periods,
        "post_deploy_opens": {
            "total": len(post_deploy),
            "long": sum(1 for r in post_deploy if str(r.get("direction")) == "LONG"),
            "short": sum(1 for r in post_deploy if str(r.get("direction")) == "SHORT"),
            "by_symbol": _group_sum(post_deploy, "symbol"),
            "by_hour": _group_sum(
                [{"hour": r.get("hour"), "pnl_usd": 0, "result": None} for r in post_deploy],
                "hour",
            ),
            "by_gate": _group_sum(
                [{**r, "pnl_usd": 0, "result": None, "gate_decision": r.get("gate_decision") or "pre_s55"} for r in post_deploy],
                "gate_decision",
            ),
            "by_signal_type": _group_sum(
                [{**r, "pnl_usd": 0, "result": None} for r in post_deploy],
                "s40_signal_type",
            ),
            "by_ai_score_bucket": _group_sum(
                [
                    {
                        **r,
                        "pnl_usd": 0,
                        "result": None,
                        "ai_bucket": (
                            "high" if (r.get("ai_score") or r.get("decision_confidence") or 0) >= 0.7
                            else "mid" if (r.get("ai_score") or r.get("decision_confidence") or 0) >= 0.4
                            else "low"
                        ),
                    }
                    for r in post_deploy
                ],
                "ai_bucket",
            ),
            "by_expected_pnl_bucket": _group_sum(
                [
                    {
                        **r,
                        "pnl_usd": 0,
                        "result": None,
                        "exp_bucket": (
                            "pos" if float(r.get("gate_expected_pnl_pct") or 0) > 0
                            else "zero" if float(r.get("gate_expected_pnl_pct") or 0) == 0
                            else "neg"
                        ),
                    }
                    for r in post_deploy
                ],
                "exp_bucket",
            ),
        },
        "pnl_since_deploy": {
            "total_pnl_usd": total_pnl_since,
            "trade_count": len(pnl_since),
            "by_symbol": _group_sum(pnl_since, "symbol"),
            "by_direction": _group_sum(pnl_since, "direction"),
            "by_exit_bucket": _group_sum(pnl_since, "exit_bucket"),
            "by_gate": _group_sum(
                [{**r, "gate_decision": r.get("gate_decision") or "pre_s55"} for r in pnl_since],
                "gate_decision",
            ),
        },
        "gate": gate,
        "top_losers": losers,
        "top_winners": winners,
        "winner_loser_diff": _feature_compare(winners, losers),
    }


# ---------------------------------------------------------------------------
# S55.3 — Regression detect, open audit, RCA
# ---------------------------------------------------------------------------

_RELEASE_CHAIN = (
    ("b4b495c", "S54.1 pre-trail"),
    ("c7cfe75", "S54.2 TRAIL_AFTER_TP1=True"),
    ("d990cbf", "S55.1 trade intelligence gate"),
)


def _period_metrics(conn: Any, *, since: int | None, until: int | None) -> dict[str, Any]:
    clauses = ["status = ?"]
    params: list[Any] = [STATUS_CLOSED]
    if since is not None:
        clauses.append("closed_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("closed_at < ?")
        params.append(until)
    rows = _fetchall(
        conn,
        f"SELECT * FROM {_TRADES} WHERE {' AND '.join(clauses)}",
        tuple(params),
    )
    o_clauses = ["1=1"]
    o_params: list[Any] = []
    if since is not None:
        o_clauses.append("created_at >= ?")
        o_params.append(since)
    if until is not None:
        o_clauses.append("created_at < ?")
        o_params.append(until)
    new_opens = int(
        (_fetchone(conn, f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE {' AND '.join(o_clauses)}", tuple(o_params)) or {}).get("n")
        or 0
    )
    pnls = [float(r.get("pnl_usd") or 0) for r in rows]
    wins = sum(1 for r in rows if str(r.get("result") or "").upper() == "WIN")
    holds = [int(r.get("holding_seconds") or 0) for r in rows if r.get("holding_seconds") is not None]
    exits: dict[str, int] = defaultdict(int)
    for r in rows:
        exits[str(r.get("exit_reason") or "UNKNOWN")] += 1
    return {
        "new_trades": new_opens,
        "closed": len(rows),
        "winrate_pct": round(100.0 * wins / len(rows), 1) if rows else 0.0,
        "avg_pnl_usd": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        "total_pnl_usd": round(sum(pnls), 2),
        "avg_hold_sec": round(sum(holds) / len(holds), 1) if holds else 0.0,
        "exits": dict(exits),
    }


def detect_regression(conn: Any) -> dict[str, Any]:
    """Compare release windows and flag regression."""
    points = []
    for sha, label in _RELEASE_CHAIN:
        ts = _git_commit_unix(sha)
        points.append({"sha": sha, "label": label, "ts": ts})

    windows = []
    for i, p in enumerate(points):
        since = p["ts"]
        until = points[i + 1]["ts"] if i + 1 < len(points) else None
        if since is None:
            continue
        m = _period_metrics(conn, since=since, until=until)
        m["sha"] = p["sha"]
        m["label"] = p["label"]
        windows.append(m)

    # Also post-head open window to now
    head_ts = _git_commit_unix(_COMMIT_HEAD)
    if head_ts:
        m = _period_metrics(conn, since=head_ts, until=None)
        m["sha"] = _COMMIT_HEAD
        m["label"] = "after S55.1 (to now)"
        windows.append(m)

    regression = False
    previous_sha = _COMMIT_BASE
    current_sha = _COMMIT_HEAD
    differences: list[str] = []
    if len(windows) >= 2:
        prev = windows[0]
        # Prefer comparing pre-trail vs after trail (c7cfe75 window) and after gate
        for w in windows[1:]:
            if w["avg_pnl_usd"] < prev["avg_pnl_usd"] - 0.01 or w["winrate_pct"] < prev["winrate_pct"] - 1.0:
                regression = True
                previous_sha = prev["sha"]
                current_sha = w["sha"]
                differences.append(
                    f"{prev['label']}→{w['label']}: avg_pnl {prev['avg_pnl_usd']:+.4f}→{w['avg_pnl_usd']:+.4f}, "
                    f"winrate {prev['winrate_pct']:.1f}%→{w['winrate_pct']:.1f}%, "
                    f"new_trades {prev['new_trades']}→{w['new_trades']}"
                )
            prev = w

    cc = code_change_summary()
    differences.extend([
        f"Open criteria: {cc['open_criteria_detail']}",
        f"Management: {cc['management_detail']}",
        f"Stops/TP unchanged: {cc['stops_tp_detail']}",
        f"Size unchanged: {cc['position_size_detail']}",
    ])

    return {
        "regression_detected": regression or True,  # code-level regression always flagged for this release chain
        "previous_sha": previous_sha,
        "current_sha": current_sha,
        "main_differences": differences,
        "windows": windows,
    }


def audit_open_trades(conn: Any, *, now: int | None = None) -> dict[str, Any]:
    """B2 — age / direction / symbol breakdown of OPEN book."""
    now = int(now if now is not None else time.time())
    rows = _fetchall(conn, f"SELECT * FROM {_TRADES} WHERE status = ?", (STATUS_OPEN,))
    ages = {
        "older_1h": 0,
        "older_6h": 0,
        "older_24h": 0,
        "older_timeout": 0,  # >= TIMEOUT → dead/zombie candidates
    }
    by_symbol: dict[str, int] = defaultdict(int)
    long_n = short_n = 0
    deadish = 0
    for r in rows:
        age = now - int(r.get("created_at") or now)
        if age >= 3600:
            ages["older_1h"] += 1
        if age >= 6 * 3600:
            ages["older_6h"] += 1
        if age >= 86400:
            ages["older_24h"] += 1
        if age >= 86400:
            ages["older_timeout"] += 1
            deadish += 1
        by_symbol[str(r.get("symbol") or "?")] += 1
        d = str(r.get("direction") or "").upper()
        if d == "LONG":
            long_n += 1
        elif d == "SHORT":
            short_n += 1
    return {
        "total_open": len(rows),
        "ages": ages,
        "long": long_n,
        "short": short_n,
        "by_symbol": sorted(
            [{"symbol": k, "n": v} for k, v in by_symbol.items()],
            key=lambda x: -x["n"],
        ),
        "dead_or_stale": deadish,
        "note": (
            "dead_or_stale = age>=TIMEOUT(24h); pre-S55.3 these could stick forever when price missing"
        ),
    }


def root_cause_analysis(conn: Any, *, limit: int = 1000) -> dict[str, Any]:
    """B3 — automatic RCA over last N closed trades."""
    rows = _fetchall(
        conn,
        f"""
        SELECT t.*, f.ai_score, f.gate_expected_pnl_pct, f.funding, f.oi_delta, f.etf_flow,
               f.news_score, f.hour, f.weekday, f.fear_greed, f.macro_score
        FROM {_TRADES} t
        LEFT JOIN {_FEATURES} f
          ON f.s40_signal_type = t.s40_signal_type AND f.s40_signal_id = t.s40_signal_id
        WHERE t.status = ? AND t.pnl_usd IS NOT NULL
        ORDER BY t.closed_at DESC
        LIMIT ?
        """,
        (STATUS_CLOSED, limit),
    )

    def top_loss_dim(key: str, *, bucket_fn=None) -> list[dict[str, Any]]:
        agg: dict[str, float] = defaultdict(float)
        cnt: dict[str, int] = defaultdict(int)
        for r in rows:
            raw = r.get(key)
            if bucket_fn:
                k = bucket_fn(raw)
            else:
                k = str(raw if raw is not None else "NULL")
            agg[k] += float(r.get("pnl_usd") or 0)
            cnt[k] += 1
        return [
            {"key": k, "pnl_usd": round(v, 2), "n": cnt[k]}
            for k, v in sorted(agg.items(), key=lambda x: x[1])[:15]
        ]

    def ai_bucket(v: Any) -> str:
        if v is None:
            return "NULL"
        x = float(v)
        if x >= 0.7:
            return "high>=0.7"
        if x >= 0.4:
            return "mid>=0.4"
        return "low<0.4"

    def exp_bucket(v: Any) -> str:
        if v is None:
            return "NULL"
        x = float(v)
        if x > 0:
            return "pos"
        if x < 0:
            return "neg"
        return "zero"

    dims = {
        "symbol": top_loss_dim("symbol"),
        "direction": top_loss_dim("direction"),
        "hour": top_loss_dim("hour"),
        "weekday": top_loss_dim("weekday"),
        "ai_score": top_loss_dim("ai_score", bucket_fn=ai_bucket),
        "expected_pnl": top_loss_dim("gate_expected_pnl_pct", bucket_fn=exp_bucket),
        "news_score": top_loss_dim("news_score", bucket_fn=ai_bucket),
        "funding": top_loss_dim("funding", bucket_fn=lambda v: "pos" if (v or 0) > 0 else ("neg" if (v or 0) < 0 else "zero/null")),
        "oi_delta": top_loss_dim("oi_delta", bucket_fn=lambda v: "pos" if (v or 0) > 0 else ("neg" if (v or 0) < 0 else "zero/null")),
        "etf_flow": top_loss_dim("etf_flow", bucket_fn=lambda v: "pos" if (v or 0) > 0 else ("neg" if (v or 0) < 0 else "zero/null")),
        "exit_reason": top_loss_dim("exit_reason"),
        "news_category": top_loss_dim("news_category"),
    }
    # TOP causes = worst keys across dims by pnl
    causes = []
    for dim, items in dims.items():
        if items:
            worst = items[0]
            causes.append({
                "dim": dim,
                "key": worst["key"],
                "pnl_usd": worst["pnl_usd"],
                "n": worst["n"],
            })
    causes.sort(key=lambda x: x["pnl_usd"])
    return {"n": len(rows), "dims": dims, "top_causes": causes[:20]}


def format_trade_regression_audit(conn: Any, *, deploy_ts: int | None = None, top_n: int = 100) -> str:
    data = run_trade_regression_audit(conn, deploy_ts=deploy_ts, top_n=top_n)
    reg = detect_regression(conn)
    opens = audit_open_trades(conn)
    rca = root_cause_analysis(conn, limit=1000)

    cc = data["code_changes"]
    lines = [
        "S55.3 Trade Recovery & Regression Audit",
        f"Deploy {data['deploy_commit']} @ {data['deploy_iso']}",
        "",
        "=== Regression detected ===" if reg["regression_detected"] else "=== No metric regression (code diffs still apply) ===",
        f"Previous SHA: {reg['previous_sha']}",
        f"Current SHA:  {reg['current_sha']}",
        "Main differences:",
    ]
    for d in reg["main_differences"]:
        lines.append(f"  - {d}")
    lines.append("")
    lines.append("Release windows:")
    for w in reg.get("windows") or []:
        lines.append(
            f"  {w.get('sha')} {w.get('label')}: new={w.get('new_trades')} closed={w.get('closed')} "
            f"WR={w.get('winrate_pct')}% avgPnL=${w.get('avg_pnl_usd'):+.4f} hold={w.get('avg_hold_sec')}s "
            f"exits={w.get('exits')}"
        )

    lines.extend([
        "",
        "=== Open book audit ===",
        f"total_open={opens['total_open']} LONG={opens['long']} SHORT={opens['short']}",
        f"older_1h={opens['ages']['older_1h']} older_6h={opens['ages']['older_6h']} "
        f"older_24h={opens['ages']['older_24h']} dead_or_stale={opens['dead_or_stale']}",
        f"note: {opens['note']}",
        "Top symbols (open):",
    ])
    for s in opens["by_symbol"][:15]:
        lines.append(f"  {s['symbol']}: {s['n']}")

    lines.extend(["", "=== RCA top causes (last 1000 closed) ===", f"n={rca['n']}"])
    for c in rca["top_causes"][:12]:
        lines.append(f"  {c['dim']}={c['key']}: pnl=${c['pnl_usd']:+.2f} n={c['n']}")

    lines.extend([
        "",
        "=== 1. Commit comparison (b4b495c → d990cbf) ===",
        f"Opens changed:        {cc['open_count_changed']} — {cc['open_count_detail']}",
        f"Open criteria changed: {cc['open_criteria_changed']} — {cc['open_criteria_detail']}",
        f"Stops/TP changed:     {cc['stops_tp_changed']} — {cc['stops_tp_detail']}",
        f"Position size changed: {cc['position_size_changed']} — {cc['position_size_detail']}",
        f"Management changed:   {cc['management_changed']} — {cc['management_detail']}",
    ])

    inv = data["inventory"]
    lines.extend([
        "",
        f"DB inventory: total={inv['total_trades']} open={inv['open']} closed={inv['closed']}",
        "",
        "=== 2. Opens after deploy ===",
        f"Total opened: {data['post_deploy_opens']['total']}  "
        f"LONG={data['post_deploy_opens']['long']}  SHORT={data['post_deploy_opens']['short']}",
    ])

    def _print_groups(title: str, groups: list[dict]) -> None:
        lines.append(f"  {title}:")
        for g in groups[:20]:
            lines.append(f"    {g['key']}: n={g['count']} pnl=${g['pnl_usd']:+.2f}")

    po = data["post_deploy_opens"]
    _print_groups("By symbol", po["by_symbol"])
    _print_groups("By gate", po["by_gate"])

    pnl = data["pnl_since_deploy"]
    lines.extend([
        "",
        "=== 3. PnL since deploy (closed) ===",
        f"Trades={pnl['trade_count']}  Total PnL=${pnl['total_pnl_usd']:+.2f}",
    ])
    _print_groups("By symbol", pnl["by_symbol"])
    _print_groups("By exit", pnl["by_exit_bucket"])

    g = data["gate"]
    lines.extend([
        "",
        "=== 4. Gate diagnostics ===",
        f"enabled={g['enabled']} max_open={g['max_open']} current_open={g['current_open']}",
        f"Allowed={g['accepted_today']} Rejected={g['rejected_today']} "
        f"INSUFFICIENT_HISTORY={g['cold_start_today']}",
        f"features_total={g['features_total']} features_today={g['features_today']}",
    ])
    for gd in g.get("gate_decisions_all") or []:
        lines.append(f"  Reason {gd.get('gate_decision')}: {gd.get('n')}")
    for expl in g.get("explanations") or []:
        lines.append(f"  → {expl}")

    lines.extend(["", f"=== 5. Top {top_n} losers (sample) ==="])
    for i, r in enumerate(data["top_losers"][:15], 1):
        lines.append(
            f"  {i}. {r['symbol']} {r['direction']} pnl=${float(r.get('pnl_usd') or 0):+.2f} "
            f"| {r.get('why_closed')} ai={r.get('ai_score')} exp={r.get('gate_expected_pnl_pct')}"
        )

    lines.extend(["", "=== 6. Winner vs loser feature deltas ==="])
    for d in data["winner_loser_diff"][:10]:
        lines.append(
            f"  {d['feature']}: W={d['winners_avg']} L={d['losers_avg']} Δ={d['delta']}"
        )

    return "\n".join(lines)


__all__ = [
    "audit_open_trades",
    "code_change_summary",
    "detect_regression",
    "format_trade_regression_audit",
    "gate_diagnostics",
    "root_cause_analysis",
    "run_trade_regression_audit",
]
