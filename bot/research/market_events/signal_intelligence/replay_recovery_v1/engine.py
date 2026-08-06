"""Replay Recovery Investigation V1 engine — research-only."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_write_manager import research_write_batch
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.engine import (
    load_funnel_candidates,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_stats_from_pnls,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.classify import (
    REPLAY_FLOOR,
    outcome_bucket,
    pnl_of,
    reject_reason,
    replay_rejects,
    replay_score,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.features import (
    compare_categorical,
    compare_numeric,
    max_drawdown,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.proposals import (
    propose_minimal_floors,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.schema import (
    TABLE,
    ensure_replay_recovery_schema,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    load_research_lake_rows,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "replay_recovery_v1"


def _enrich_with_lake(candidates: list[dict[str, Any]], conn: Any) -> list[dict[str, Any]]:
    lake = load_research_lake_rows(conn, require_pnl=False)
    by_id = {int(r.get("trade_id") or 0): r for r in lake if int(r.get("trade_id") or 0)}
    out: list[dict[str, Any]] = []
    for r in candidates:
        tid = int(r.get("trade_id") or 0)
        merged = dict(r)
        lake_row = by_id.get(tid) or {}
        for k, v in lake_row.items():
            if merged.get(k) is None and v is not None:
                merged[k] = v
        # prefer journal module scores already on r
        out.append(merged)
    return out


def confusion_matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Replay decision vs outcome:
      TP = accept & winner, FP = accept & loser,
      FN = reject & winner (missed), TN = reject & loser (saved)
    """
    tp = fp = fn = tn = unk = 0
    for r in rows:
        rej = replay_rejects(r)
        bucket = outcome_bucket(r)
        if bucket == "unknown":
            unk += 1
            continue
        win = bucket == "winner"
        if not rej and win:
            tp += 1
        elif not rej and not win:
            fp += 1
        elif rej and win:
            fn += 1
        else:
            tn += 1
    return {
        "tp_accepted_winners": tp,
        "fp_accepted_losers": fp,
        "fn_missed_winners": fn,
        "tn_saved_losers": tn,
        "unknown_pnl": unk,
        "n": len(rows),
    }


def reject_histogram(rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    c: Counter[str] = Counter(reject_reason(r) for r in rejected)
    return [{"reason": k, "n": v} for k, v in c.most_common()]


def build_replay_sets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rejected = [r for r in rows if replay_rejects(r)]
    accepted = [r for r in rows if not replay_rejects(r)]
    missed = [r for r in rejected if outcome_bucket(r) == "winner"]
    saved = [r for r in rejected if outcome_bucket(r) == "loser"]
    flat_rej = [r for r in rejected if outcome_bucket(r) == "flat"]

    missed_pnls = [float(pnl_of(r) or 0) for r in missed]
    saved_pnls = [float(pnl_of(r) or 0) for r in saved]
    recoverable_ev = round(sum(missed_pnls), 6)
    protected_ev = round(sum(abs(p) for p in saved_pnls), 6)
    net_replay_ev = round(protected_ev - recoverable_ev, 6)

    largest_mistake = None
    if missed:
        largest_mistake = max(missed, key=lambda r: float(pnl_of(r) or 0))

    return {
        "n_total": len(rows),
        "n_rejected": len(rejected),
        "n_accepted": len(accepted),
        "n_missed_winners": len(missed),
        "n_saved_losers": len(saved),
        "n_flat_rejected": len(flat_rej),
        "recoverable_ev": recoverable_ev,
        "protected_ev": protected_ev,
        "net_replay_ev": net_replay_ev,
        "rejected": rejected,
        "accepted": accepted,
        "missed_winners": missed,
        "saved_losers": saved,
        "largest_mistake": {
            "trade_id": int(largest_mistake.get("trade_id") or 0) if largest_mistake else None,
            "symbol": largest_mistake.get("symbol") if largest_mistake else None,
            "pnl": pnl_of(largest_mistake) if largest_mistake else None,
            "replay": replay_score(largest_mistake) if largest_mistake else None,
            "reason": reject_reason(largest_mistake) if largest_mistake else None,
            "regime": (largest_mistake.get("regime") or largest_mistake.get("market_regime"))
            if largest_mistake else None,
        } if largest_mistake else None,
        "missed_stats": book_stats_from_pnls(missed_pnls),
        "saved_stats": book_stats_from_pnls(saved_pnls),
        "accepted_stats": book_stats_from_pnls(
            [float(pnl_of(r)) for r in accepted if pnl_of(r) is not None]
        ),
        "accepted_max_dd": max_drawdown(
            [float(pnl_of(r)) for r in accepted if pnl_of(r) is not None]
        ),
    }


def attach_reality(rows: list[dict[str, Any]], reality_score: float | None) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        m = dict(r)
        if m.get("reality_score") is None:
            m["reality_score"] = reality_score
        out.append(m)
    return out


def persist_recovery(conn: Any, result: dict[str, Any]) -> int:
    ensure_replay_recovery_schema(conn)
    now = int(time.time())
    rows_flat: list[tuple[Any, ...]] = []

    def add(section: str, key: str, value_real: Any = None, value_text: Any = None, meta: Any = None):
        rows_flat.append((
            section, key,
            float(value_real) if isinstance(value_real, (int, float)) else None,
            None if value_text is None else str(value_text),
            json.dumps(meta, default=str) if meta is not None else None,
            now,
        ))

    add("summary", "recoverable_ev", result.get("recoverable_ev"))
    add("summary", "protected_ev", result.get("protected_ev"))
    add("summary", "net_replay_ev", result.get("net_replay_ev"))
    add("summary", "n_rejected", result.get("n_rejected"))
    add("summary", "n_missed_winners", result.get("n_missed_winners"))
    add("summary", "n_saved_losers", result.get("n_saved_losers"))
    add("summary", "replay_floor", REPLAY_FLOOR)
    add("summary", "elapsed_sec", result.get("elapsed_sec"))
    add("confusion", "payload", meta=result.get("confusion"))
    add("histogram", "payload", meta=result.get("reject_histogram"))
    add("features_numeric", "payload", meta=result.get("feature_numeric"))
    add("features_regime", "payload", meta=result.get("feature_regime"))
    add("features_coin", "payload", meta=result.get("feature_coin"))
    add("features_session", "payload", meta=result.get("feature_session"))
    add("proposals", "payload", meta=result.get("proposals"))
    add("largest_mistake", "payload", meta=result.get("largest_mistake"))
    add("why", "payload", meta=result.get("why_rejected"))

    def _write(c: Any) -> int:
        c.execute(f"DELETE FROM {TABLE}")
        c.executemany(
            f"""
            INSERT INTO {TABLE}(section, key, value_real, value_text, meta_json, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            rows_flat,
        )
        return len(rows_flat)

    return research_write_batch(conn, _write)


def write_recovery_reports(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cm = result.get("confusion") or {}
    hist = result.get("reject_histogram") or []
    lm = result.get("largest_mistake") or {}
    props = result.get("proposals") or {}
    best = props.get("best_feasible") or {}

    recovery = "\n".join([
        "# REPLAY_RECOVERY",
        "",
        "_Replay Recovery Investigation V1 — research only. Replay/Decision/Gate unchanged._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- replay_floor: **{REPLAY_FLOOR}**",
        f"- candidates: **{result.get('n_total')}**",
        f"- rejected_by_replay: **{result.get('n_rejected')}**",
        f"- missed_winners (FN): **{result.get('n_missed_winners')}**",
        f"- saved_losers (TN): **{result.get('n_saved_losers')}**",
        f"- recoverable EV (lost winners): **{result.get('recoverable_ev')}**",
        f"- protected EV (blocked losers): **{result.get('protected_ev')}**",
        f"- net Replay EV (protected − recoverable): **{result.get('net_replay_ev')}**",
        f"- largest replay mistake: trade `{lm.get('trade_id')}` "
        f"{lm.get('symbol')} pnl={lm.get('pnl')} replay={lm.get('replay')} "
        f"reason={lm.get('reason')}",
        "",
        "## Confusion matrix",
        f"- TP accepted winners: {cm.get('tp_accepted_winners')}",
        f"- FP accepted losers: {cm.get('fp_accepted_losers')}",
        f"- FN missed winners: {cm.get('fn_missed_winners')}",
        f"- TN saved losers: {cm.get('tn_saved_losers')}",
        "",
        "## Why Replay rejected (histogram)",
        "",
        "| Reason | n |",
        "|--------|--:|",
        *[f"| {h['reason']} | {h['n']} |" for h in hist],
        "",
        "## Minimal floor proposals (do not apply)",
        f"- baseline floor={REPLAY_FLOOR} recoverable_ev={((props.get('baseline') or {}).get('recoverable_ev_if_reject'))}",
        f"- best feasible: {best.get('floor')} "
        f"ev_recovered={best.get('ev_recovered_vs_baseline')} "
        f"dd_increase_pct={best.get('dd_increase_pct_vs_baseline')}",
        "",
        "research_only=true observe_only=true",
        "",
    ])

    errors = "\n".join([
        "# REPLAY_ERRORS",
        "",
        "## Missed winners (false rejects) — top by PnL",
        "",
        "| trade_id | symbol | pnl | replay | reason | regime |",
        "|---------:|--------|----:|-------:|--------|--------|",
        *[
            f"| {r.get('trade_id')} | {r.get('symbol')} | {pnl_of(r)} | "
            f"{replay_score(r)} | {reject_reason(r)} | "
            f"{r.get('regime') or r.get('market_regime')} |"
            for r in sorted(
                result.get("missed_winners_sample") or [],
                key=lambda x: float(pnl_of(x) or 0),
                reverse=True,
            )[:40]
        ],
        "",
        "## Feature deltas (missed winners − saved losers)",
        "",
        "| Feature | mean_winner | mean_loser | Δ |",
        "|---------|------------:|-----------:|--:|",
        *[
            f"| {f['feature']} | {f.get('mean_winner')} | {f.get('mean_loser')} | "
            f"{f.get('delta_w_minus_l')} |"
            for f in (result.get("feature_numeric") or [])
            if f.get("n_winners") or f.get("n_losers")
        ],
        "",
        "## Regime / coin / session",
        f"```json\n{json.dumps({'regime': result.get('feature_regime'), 'coin': result.get('feature_coin'), 'session': result.get('feature_session')}, indent=2, default=str)}\n```",
        "",
    ])

    recoverable = "\n".join([
        "# REPLAY_RECOVERABLE",
        "",
        f"- recoverable_ev: **{result.get('recoverable_ev')}**",
        f"- protected_ev: **{result.get('protected_ev')}**",
        f"- n_missed_winners: **{result.get('n_missed_winners')}**",
        f"- n_saved_losers: **{result.get('n_saved_losers')}**",
        "",
        "## Interpretation",
        "",
        result.get("why_rejected") or "",
        "",
        "## Proposal grid",
        f"```json\n{json.dumps(props.get('grid') or [], indent=2, default=str)[:8000]}\n```",
        "",
    ])

    paths = {
        "REPLAY_RECOVERY.md": str(BASE_DIR / "REPLAY_RECOVERY.md"),
        "REPLAY_ERRORS.md": str(BASE_DIR / "REPLAY_ERRORS.md"),
        "REPLAY_RECOVERABLE.md": str(BASE_DIR / "REPLAY_RECOVERABLE.md"),
    }
    (BASE_DIR / "REPLAY_RECOVERY.md").write_text(recovery, encoding="utf-8")
    (BASE_DIR / "REPLAY_ERRORS.md").write_text(errors, encoding="utf-8")
    (BASE_DIR / "REPLAY_RECOVERABLE.md").write_text(recoverable, encoding="utf-8")
    for name, text in (
        ("REPLAY_RECOVERY.md", recovery),
        ("REPLAY_ERRORS.md", errors),
        ("REPLAY_RECOVERABLE.md", recoverable),
    ):
        (OUT_DIR / name).write_text(text, encoding="utf-8")
    jp = OUT_DIR / "replay_recovery.json"
    slim = {k: result.get(k) for k in (
        "ok", "elapsed_sec", "n_total", "n_rejected", "n_missed_winners", "n_saved_losers",
        "recoverable_ev", "protected_ev", "net_replay_ev", "confusion", "reject_histogram",
        "largest_mistake", "feature_numeric", "proposals", "why_rejected",
    )}
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


def _why_text(result: dict[str, Any]) -> str:
    hist = result.get("reject_histogram") or []
    top = hist[0]["reason"] if hist else "unknown"
    return (
        f"Replay rejects when score is missing or < {REPLAY_FLOOR}. "
        f"Dominant reject bucket: **{top}**. "
        f"Recoverable EV={result.get('recoverable_ev')} comes from FN missed winners; "
        f"protected EV={result.get('protected_ev')} from TN saved losers. "
        f"Net Replay EV={result.get('net_replay_ev')} "
        f"(positive ⇒ Replay still net-protective on this corpus)."
    )


def format_terminal(result: dict[str, Any]) -> str:
    lm = result.get("largest_mistake") or {}
    return "\n".join([
        "REPLAY RECOVERY INVESTIGATION V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s rejected={result.get('n_rejected')}",
        f"recoverable_ev={result.get('recoverable_ev')}",
        f"protected_ev={result.get('protected_ev')}",
        f"net_replay_ev={result.get('net_replay_ev')}",
        f"largest_mistake=trade:{lm.get('trade_id')} pnl={lm.get('pnl')} "
        f"reason={lm.get('reason')}",
        "",
        "research_only=true observe_only=true no_replay_change=true",
    ])


def run_replay_recovery_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_replay_recovery_schema(conn)
    candidates, ctx = load_funnel_candidates(conn)
    candidates = attach_reality(candidates, ctx.get("reality_score"))
    candidates = _enrich_with_lake(candidates, conn)

    sets = build_replay_sets(candidates)
    confusion = confusion_matrix(candidates)
    histogram = reject_histogram(sets["rejected"])
    feature_numeric = compare_numeric(sets["missed_winners"], sets["saved_losers"])
    feature_regime = compare_categorical(
        sets["missed_winners"], sets["saved_losers"], key="regime"
    )
    feature_coin = compare_categorical(
        sets["missed_winners"], sets["saved_losers"], key="symbol"
    )
    feature_session = compare_categorical(
        sets["missed_winners"], sets["saved_losers"], key="session"
    )
    proposals = propose_minimal_floors(candidates)

    # top missed winners sample for report
    missed_sample = sorted(
        sets["missed_winners"],
        key=lambda r: float(pnl_of(r) or 0),
        reverse=True,
    )[:200]

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": True,
        "research_only": True,
        "observe_only": True,
        "replay_unchanged": True,
        "execution_unchanged": True,
        "elapsed_sec": elapsed,
        "replay_floor": REPLAY_FLOOR,
        "n_total": sets["n_total"],
        "n_rejected": sets["n_rejected"],
        "n_accepted": sets["n_accepted"],
        "n_missed_winners": sets["n_missed_winners"],
        "n_saved_losers": sets["n_saved_losers"],
        "recoverable_ev": sets["recoverable_ev"],
        "protected_ev": sets["protected_ev"],
        "net_replay_ev": sets["net_replay_ev"],
        "confusion": confusion,
        "reject_histogram": histogram,
        "feature_numeric": feature_numeric,
        "feature_regime": feature_regime,
        "feature_coin": feature_coin,
        "feature_session": feature_session,
        "proposals": proposals,
        "largest_mistake": sets["largest_mistake"],
        "missed_winners_sample": missed_sample,
        "missed_stats": sets["missed_stats"],
        "saved_stats": sets["saved_stats"],
        "accepted_stats": sets["accepted_stats"],
        "reality_score": ctx.get("reality_score"),
    }
    result["why_rejected"] = _why_text(result)
    result["terminal"] = format_terminal(result)

    if persist:
        result["persisted"] = persist_recovery(conn, result)
    if write_reports:
        result["paths"] = write_recovery_reports(result)
    return result


__all__ = [
    "build_replay_sets",
    "confusion_matrix",
    "format_terminal",
    "reject_histogram",
    "run_replay_recovery_v1",
    "write_recovery_reports",
]
