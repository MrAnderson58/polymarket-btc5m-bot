"""Phase G.4.0.1 — Shadow Pipeline Diagnostics and traced execution."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candidate_g31 import CandidateG31
from bot.research.market_events.signal_intelligence.config import (
    G40_SHADOW_ENABLED,
    G40_SHADOW_MAX_PER_CYCLE,
    G40_SHADOW_MAX_PER_DAY,
)
from bot.research.market_events.signal_intelligence.shadow_g40 import (
    SHADOW_HEADER,
    SHADOW_HORIZON_SECS,
    SHADOW_PROFILE,
    ShadowSignalG40,
    _claude_summary_at_signal,
    _open_shadow_symbols,
    _shadow_sent_today,
    format_shadow_telegram_g40,
    is_shadow_only_g40,
    passes_shadow_g40,
    persist_shadow_signal_g40,
    pick_shadow_candidates_g40,
    send_shadow_telegram_g40,
    threshold_failures_g39,
)

logger = logging.getLogger(__name__)

_TRACE_TABLE = "market_shadow_pipeline_trace"
_SIGNALS = "market_shadow_signals"


class ShadowPipelineError(Exception):
    """Shadow enabled + eligible candidate but persist failed."""


@dataclass
class ShadowTraceStepG401:
    step: str
    status: str
    reason: str | None = None


@dataclass
class ShadowPipelineResultG401:
    symbol: str
    steps: list[ShadowTraceStepG401] = field(default_factory=list)
    signal_id: int | None = None
    signal: ShadowSignalG40 | None = None
    error: str | None = None

    def add(self, step: str, status: str, reason: str | None = None) -> None:
        self.steps.append(ShadowTraceStepG401(step=step, status=status, reason=reason))

    @property
    def outcome(self) -> str:
        if self.error:
            return "ERROR"
        persist = next((s for s in self.steps if s.step == "Persist"), None)
        if persist and persist.status in ("OK", "PASS"):
            return "CREATED"
        if persist and persist.status == "SKIPPED":
            return "SKIPPED"
        if persist and persist.status == "FAIL":
            return "FAILED"
        return "IN_PROGRESS"


def _candidate_ok(c: CandidateG31) -> bool:
    return c.confidence is not None and c.market_score is not None and c.trend is not None


def _thresholds_status(candidate: CandidateG31) -> tuple[str, str | None]:
    if not passes_shadow_g40(candidate):
        fails = threshold_failures_g39(candidate, SHADOW_PROFILE)
        reason = fails[0]["gate"] if fails else "shadow thresholds not met"
        return "FAIL", reason
    if not is_shadow_only_g40(candidate):
        return "SKIP", "already passes production"
    return "PASS", None


def build_shadow_trace_g401(
    conn: Any,
    *,
    candidate: CandidateG31,
    snapshot_id: int,
    event_id: int | None = None,
    cycle_context: dict[str, Any] | None = None,
) -> ShadowPipelineResultG401:
    """Build trace steps for one candidate (dry-run through gates)."""
    result = ShadowPipelineResultG401(symbol=candidate.symbol)
    ctx = cycle_context or {}

    result.add(
        "Candidate",
        "YES" if _candidate_ok(candidate) else "NO",
        None if _candidate_ok(candidate) else "missing scores or trend",
    )

    if not G40_SHADOW_ENABLED:
        result.add("Shadow Enabled", "NO", "ME_SHADOW_ENABLED false")
        result.add("Thresholds", "SKIP", "shadow disabled")
        result.add("Persist", "SKIPPED", "ME_SHADOW_ENABLED false")
        result.add("Telegram", "SKIP", "shadow disabled")
        result.add("Followup scheduled", "NO", "shadow disabled")
        logger.info("SHADOW SKIPPED reason=ME_SHADOW_ENABLED false symbol=%s", candidate.symbol)
        return result

    result.add("Shadow Enabled", "YES")

    thr_status, thr_reason = _thresholds_status(candidate)
    result.add("Thresholds", thr_status, thr_reason)

    if thr_status != "PASS":
        result.add("Persist", "SKIPPED", thr_reason or "not shadow-eligible")
        result.add("Telegram", "SKIP", thr_reason or "not shadow-eligible")
        result.add("Followup scheduled", "NO", thr_reason or "not shadow-eligible")
        if thr_status == "FAIL":
            logger.info("SHADOW SKIPPED reason=%s symbol=%s", thr_reason, candidate.symbol)
        return result

    skip_reason = _persist_skip_reason(conn, candidate=candidate, ctx=ctx)
    if skip_reason:
        result.add("Persist", "SKIPPED", skip_reason)
        result.add("Telegram", "SKIP", skip_reason)
        result.add("Followup scheduled", "NO", skip_reason)
        logger.info("SHADOW SKIPPED reason=%s symbol=%s", skip_reason, candidate.symbol)
        return result

    return result


def _persist_skip_reason(
    conn: Any,
    *,
    candidate: CandidateG31,
    ctx: dict[str, Any],
) -> str | None:
    if ctx.get("daily_cap_reached"):
        return "daily cap reached"
    if ctx.get("not_in_pick_list"):
        return "not in cycle pick list"
    if candidate.symbol in ctx.get("open_symbols", set()):
        return "open shadow signal exists"
    if ctx.get("recent_symbol"):
        return "recent signal within 2h"
    sent = _shadow_sent_today(conn)
    if sent >= G40_SHADOW_MAX_PER_DAY:
        return "daily cap reached"
    return None


def execute_shadow_pipeline_g401(
    conn: Any,
    *,
    candidate: CandidateG31,
    snapshot_id: int,
    event_id: int | None = None,
    cycle_context: dict[str, Any] | None = None,
    send_telegram: bool = True,
) -> ShadowPipelineResultG401:
    """Run traced shadow pipeline for one candidate; persist + telegram when eligible."""
    logger.info("SHADOW START symbol=%s snapshot_id=%s", candidate.symbol, snapshot_id)
    result = build_shadow_trace_g401(
        conn,
        candidate=candidate,
        snapshot_id=snapshot_id,
        event_id=event_id,
        cycle_context=cycle_context,
    )

    if result.outcome != "IN_PROGRESS":
        thr = next((s for s in result.steps if s.step == "Thresholds"), None)
        if thr and thr.status == "FAIL":
            logger.info("SHADOW FAILED reason=thresholds symbol=%s detail=%s", candidate.symbol, thr.reason)
        elif thr and thr.status == "SKIP":
            logger.info("SHADOW SKIPPED reason=%s symbol=%s", thr.reason, candidate.symbol)
        skip = next((s for s in result.steps if s.step == "Persist" and s.status == "SKIPPED"), None)
        if skip:
            logger.info("SHADOW SKIPPED reason=%s symbol=%s", skip.reason, candidate.symbol)
        return result

    logger.info("SHADOW THRESHOLDS PASS symbol=%s", candidate.symbol)

    try:
        sig = persist_shadow_signal_g40(
            conn, snapshot_id=snapshot_id, candidate=candidate, event_id=event_id,
        )
    except Exception as exc:
        result.add("Persist", "FAIL", f"sqlite insert failed: {exc}")
        result.add("Telegram", "SKIP", "persist failed")
        result.add("Followup scheduled", "NO", "persist failed")
        result.error = str(exc)
        logger.error("SHADOW FAILED reason=persist exception symbol=%s err=%s", candidate.symbol, exc)
        raise ShadowPipelineError(str(exc)) from exc

    if not sig:
        result.add("Persist", "FAIL", "sqlite insert failed")
        result.add("Telegram", "SKIP", "persist returned None")
        result.add("Followup scheduled", "NO", "persist returned None")
        result.error = "persist returned None"
        logger.error("SHADOW FAILED reason=persist returned None symbol=%s", candidate.symbol)
        raise ShadowPipelineError("persist returned None")

    result.signal_id = sig.signal_id
    result.signal = sig
    result.add("Persist", "PASS")
    logger.info("SHADOW DB INSERT id=%s symbol=%s", sig.signal_id, candidate.symbol)
    logger.info(
        "SHADOW CREATED id=%s symbol=%s confidence=%s market_score=%s rr=%s",
        sig.signal_id,
        candidate.symbol,
        candidate.confidence,
        candidate.market_score,
        candidate.rr,
    )

    tg_ok = False
    if send_telegram:
        try:
            tg_ok = send_shadow_telegram_g40(conn, sig)
        except Exception as exc:
            result.add("Telegram", "FAIL", str(exc))
            result.add("Followup scheduled", "YES", "signal persisted")
            logger.error("SHADOW FAILED reason=telegram symbol=%s err=%s", candidate.symbol, exc)
            return result
    result.add("Telegram", "SENT" if tg_ok else "SKIP", None if tg_ok else "alerts disabled or dedupe")
    logger.info("SHADOW TELEGRAM sent=%s id=%s symbol=%s", tg_ok, sig.signal_id, candidate.symbol)
    result.add(
        "Followup scheduled",
        "YES",
        f"horizons: {', '.join(h for h, _ in SHADOW_HORIZON_SECS)}",
    )
    logger.info("SHADOW FOLLOWUP scheduled=YES id=%s symbol=%s", sig.signal_id, candidate.symbol)
    return result


def run_shadow_pipeline_g401(
    conn: Any,
    *,
    snapshot_id: int,
    candidates: list[CandidateG31],
    event_id: int | None = None,
    candidate_ts: int | None = None,
) -> list[ShadowPipelineResultG401]:
    """Traced shadow lane — replaces silent maybe_run_shadow_lane."""
    logger.info(
        "SHADOW START lane snapshot_id=%s candidates=%s enabled=%s",
        snapshot_id, len(candidates), G40_SHADOW_ENABLED,
    )
    results: list[ShadowPipelineResultG401] = []
    ts = candidate_ts or int(time.time())

    if not G40_SHADOW_ENABLED:
        for c in candidates:
            r = build_shadow_trace_g401(conn, candidate=c, snapshot_id=snapshot_id, event_id=event_id)
            _persist_trace_g401(conn, result=r, snapshot_id=snapshot_id, candidate_ts=ts)
            results.append(r)
        return results

    sent_today = _shadow_sent_today(conn)
    daily_cap = sent_today >= G40_SHADOW_MAX_PER_DAY
    remaining = max(0, G40_SHADOW_MAX_PER_DAY - sent_today)
    per_cycle = min(G40_SHADOW_MAX_PER_CYCLE, remaining) if not daily_cap else 0
    open_syms = _open_shadow_symbols(conn)
    picked_syms = {c.symbol for c in pick_shadow_candidates_g40(candidates, limit=per_cycle)}

    ctx_base: dict[str, Any] = {
        "daily_cap_reached": daily_cap,
        "open_symbols": open_syms,
    }

    for c in candidates:
        ctx = {
            **ctx_base,
            "not_in_pick_list": c.symbol not in picked_syms and is_shadow_only_g40(c),
        }
        if c.symbol in picked_syms:
            recent = conn.execute(
                f"""
                SELECT id FROM {_SIGNALS}
                WHERE symbol = ? AND created_at >= ?
                """,
                (c.symbol, int(time.time()) - 7200),
            ).fetchone()
            ctx["recent_symbol"] = bool(recent)

        if is_shadow_only_g40(c) and c.symbol in picked_syms and not ctx.get("recent_symbol"):
            r = execute_shadow_pipeline_g401(
                conn,
                candidate=c,
                snapshot_id=snapshot_id,
                event_id=event_id,
                cycle_context=ctx,
            )
        else:
            r = build_shadow_trace_g401(
                conn,
                candidate=c,
                snapshot_id=snapshot_id,
                event_id=event_id,
                cycle_context=ctx,
            )

        _persist_trace_g401(conn, result=r, snapshot_id=snapshot_id, candidate_ts=ts)
        results.append(r)

    return results


def _persist_trace_g401(
    conn: Any,
    *,
    result: ShadowPipelineResultG401,
    snapshot_id: int,
    candidate_ts: int,
) -> None:
    payload = [
        {"step": s.step, "status": s.status, "reason": s.reason}
        for s in result.steps
    ]
    try:
        insert_returning_id(
            conn,
            f"""
            INSERT INTO {_TRACE_TABLE} (
              candidate_ts, snapshot_id, symbol, trace_json, signal_id, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_ts,
                snapshot_id,
                result.symbol,
                json.dumps(payload, ensure_ascii=False),
                result.signal_id,
                result.outcome,
                int(time.time()),
            ),
        )
    except Exception as exc:
        logger.debug("shadow trace persist skipped: %s", exc)


def format_shadow_trace_g401(conn: Any, *, symbol: str | None = None) -> str:
    from bot.research.market_events.signal_intelligence.candidate_g31 import _latest_cycle_ts

    ts = _latest_cycle_ts(conn)
    if not ts:
        return "No candidate cycles — run g3-run first."

    if symbol:
        rows = conn.execute(
            f"""
            SELECT symbol, trace_json FROM {_TRACE_TABLE}
            WHERE candidate_ts = ? AND symbol = ?
            """,
            (ts, symbol.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT symbol, trace_json FROM {_TRACE_TABLE}
            WHERE candidate_ts = ?
            ORDER BY symbol
            """,
            (ts,),
        ).fetchall()

    if not rows:
        return _reconstruct_trace_fallback(conn, candidate_ts=ts, symbol=symbol)

    parts: list[str] = []
    for row in rows:
        parts.append(_format_trace_block(str(row["symbol"]), row["trace_json"]))
    return "\n\n---\n\n".join(parts)


def _format_trace_block(symbol: str, trace_json: str) -> str:
    try:
        steps = json.loads(trace_json)
    except (json.JSONDecodeError, TypeError):
        steps = []

    lines = [symbol, ""]
    for i, step in enumerate(steps):
        if i > 0:
            lines.extend(["", "↓", ""])
        name = step.get("step", "?")
        status = step.get("status", "?")
        reason = step.get("reason")
        lines.append(name)
        lines.append(status)
        if reason:
            lines.extend(["", "reason", "", reason])
    return "\n".join(lines)


def _reconstruct_trace_fallback(
    conn: Any,
    *,
    candidate_ts: int,
    symbol: str | None,
) -> str:
    """Live trace when DB traces missing (pre-migration cycles)."""
    from bot.research.market_events.signal_intelligence.candidate_g31 import _TABLE

    if symbol:
        rows = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE candidate_ts = ? AND symbol = ?",
            (candidate_ts, symbol.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE candidate_ts = ? ORDER BY symbol LIMIT 30",
            (candidate_ts,),
        ).fetchall()

    if not rows:
        return "No shadow pipeline traces for latest cycle."

    parts: list[str] = []
    for row in rows:
        c = CandidateG31(
            symbol=str(row["symbol"]),
            trend_score=float(row["trend_score"] or 0),
            market_score=float(row["market_score"]) if row["market_score"] is not None else None,
            liquidity_score=float(row["liquidity_score"]) if row["liquidity_score"] is not None else None,
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            rr=float(row["rr"]) if row["rr"] is not None else None,
            btc_alignment=str(row["btc_alignment"] or "Neutral"),
            funding_score=float(row["funding_score"]) if row["funding_score"] is not None else None,
            oi_score=float(row["oi_score"] or 0),
            volume_score=float(row["volume_score"] or 0),
            atr_score=float(row["atr_score"] or 0),
            fear_greed=float(row["fear_greed"]) if row["fear_greed"] is not None else None,
            candidate_state=str(row["candidate_state"]),
            rejection_reason=row["rejection_reason"],
            direction=row["direction"],
            trend_coverage_pct=float(row["trend_coverage_pct"]) if row["trend_coverage_pct"] is not None else None,
            trend_windows_json=row["trend_windows_json"],
            trend=None,
        )
        r = build_shadow_trace_g401(conn, candidate=c, snapshot_id=int(row["snapshot_id"] or 0))
        parts.append(_format_trace_block(c.symbol, json.dumps(
            [{"step": s.step, "status": s.status, "reason": s.reason} for s in r.steps]
        )))
    return "\n\n---\n\n".join(parts)


def run_shadow_self_test_g401(conn: Any) -> tuple[bool, str]:
    """Synthetic candidate through full shadow pipeline."""
    from unittest.mock import patch

    from bot.research.market_events.signal_intelligence import shadow_g40 as mod
    from bot.research.market_events.signal_intelligence import shadow_pipeline_g401 as pipe_mod
    from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3

    checks: list[tuple[str, bool, str]] = []

    def _check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    trend = TrendWindowG3(
        symbol="BTC", window_minutes=15, pattern_type="slow_bleed",
        consecutive_candles=6, trend_score=40.0, direction="DOWN", details={},
    )
    candidate = CandidateG31(
        symbol="BTC",
        trend_score=40.0,
        market_score=31.0,
        liquidity_score=42.0,
        confidence=5.8,
        rr=1.7,
        btc_alignment="Neutral",
        funding_score=50.0,
        oi_score=50.0,
        volume_score=18.0,
        atr_score=50.0,
        fear_greed=50.0,
        candidate_state="rejected",
        rejection_reason="test",
        direction="SHORT",
        trend_coverage_pct=100.0,
        trend_windows_json="[]",
        trend=trend,
    )

    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_snapshots_g3 (
          snapshot_uuid, snapshot_ts, btc_price, created_at
        ) VALUES (?, ?, 65000.0, ?)
        """,
        (f"selftest-{now}", now, now),
    )
    conn.commit()
    snap_id = int(conn.execute("SELECT id FROM market_snapshots_g3 ORDER BY id DESC LIMIT 1").fetchone()["id"])

    claude = _claude_summary_at_signal(conn, symbol="BTC", candidate=candidate)
    tg = format_shadow_telegram_g40(candidate=candidate, claude_summary=claude)
    _check("Telegram render", SHADOW_HEADER in tg and "BTC" in tg)

    with patch.object(pipe_mod, "G40_SHADOW_ENABLED", True):
        with patch.object(mod, "send_shadow_telegram_g40", return_value=True):
            r = execute_shadow_pipeline_g401(
                conn,
                candidate=candidate,
                snapshot_id=snap_id,
                cycle_context={},
                send_telegram=True,
            )

    _check("Pipeline persist", r.signal_id is not None, r.error or "")
    _check("Pipeline outcome", r.outcome == "CREATED", r.outcome)

    if r.signal_id:
        row = conn.execute(
            f"SELECT * FROM {_SIGNALS} WHERE id = ?",
            (r.signal_id,),
        ).fetchone()
        _check("DB insert", row is not None)
        _check("Followup schedule", row is not None and str(row["status"]) == "OPEN")
        followup_step = next((s for s in r.steps if s.step == "Followup scheduled"), None)
        _check("Followup trace", followup_step is not None and followup_step.status == "YES")

    failed = [c for c in checks if not c[1]]
    if failed:
        lines = ["FAILED", ""]
        for name, _, detail in failed:
            lines.append(f"{name}: {detail or 'fail'}")
        return False, "\n".join(lines)
    return True, "PASS"


def format_shadow_self_test_g401(conn: Any) -> str:
    ok, msg = run_shadow_self_test_g401(conn)
    return msg
