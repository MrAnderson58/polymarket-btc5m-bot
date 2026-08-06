"""Reality Validation Engine V1 — try to DISPROVE the system (research-only)."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_write_manager import research_write_batch
from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import (
    STORE_CATEGORIES,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    load_stored_candidates,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.leave_one_out import (
    leave_one_coin_out,
    leave_one_month_out,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.monte_carlo import (
    monte_carlo_reality,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.oos import (
    out_of_sample,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.regimes import (
    regime_slices,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
    TABLE,
    ensure_reality_validation_schema,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.scores import (
    compute_overfitting_score,
    compute_reality_score,
    fail_reasons,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.stress import (
    reality_stress,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.walk_forward import (
    walk_forward,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "reality_validation_v1"

SOURCES = ("book_a", "book_b", "elite", "combined")


def _book_trades(conn: Any, book: str) -> list[dict[str, Any]]:
    rows = load_journal_rows(conn, book=book)
    out: list[dict[str, Any]] = []
    for r in rows:
        if int(r.get("accepted") or 0) != 1:
            continue
        if r.get("pnl") is None:
            continue
        out.append({
            "trade_id": int(r.get("trade_id") or 0),
            "symbol": r.get("symbol"),
            "opened_at": int(r.get("opened_at") or 0),
            "direction": r.get("direction"),
            "pnl": float(r.get("pnl")),
            "result": r.get("result"),
            "regime": r.get("regime") or r.get("current_regime") or r.get("market_regime"),
            "source": book,
        })
    out.sort(key=lambda x: int(x.get("opened_at") or 0))
    return out


def load_validation_trades(conn: Any, source: str = "combined") -> list[dict[str, Any]]:
    source = str(source).lower()
    if source == "book_a":
        return _book_trades(conn, BOOK_A)
    if source == "book_b":
        return _book_trades(conn, BOOK_B)
    if source == "elite":
        cands = load_stored_candidates(conn, categories=list(STORE_CATEGORIES))
        journal = {int(r.get("trade_id") or 0): r for r in load_journal_rows(conn, book=BOOK_B)}
        out: list[dict[str, Any]] = []
        for r in cands:
            tid = int(r.get("trade_id") or 0)
            j = journal.get(tid) or {}
            pnl = r.get("pnl")
            if pnl is None:
                pnl = j.get("pnl")
            if pnl is None:
                continue
            out.append({
                "trade_id": tid,
                "symbol": r.get("symbol") or j.get("symbol"),
                "opened_at": int(r.get("opened_at") or j.get("opened_at") or 0),
                "direction": r.get("direction") or j.get("direction"),
                "pnl": float(pnl),
                "result": r.get("result") or j.get("result"),
                "regime": r.get("current_regime") or j.get("regime"),
                "source": "elite",
                "category": r.get("category"),
            })
        out.sort(key=lambda x: int(x.get("opened_at") or 0))
        return out
    # combined: Book B preferred + Book A + elite (dedupe by trade_id, keep first)
    seen: set[int] = set()
    merged: list[dict[str, Any]] = []
    for block in (
        _book_trades(conn, BOOK_B),
        _book_trades(conn, BOOK_A),
        load_validation_trades(conn, "elite"),
    ):
        for t in block:
            tid = int(t.get("trade_id") or 0)
            if tid and tid in seen:
                continue
            if tid:
                seen.add(tid)
            merged.append(t)
    merged.sort(key=lambda x: int(x.get("opened_at") or 0))
    return merged


def persist_reality(conn: Any, *, rows: list[dict[str, Any]]) -> int:
    ensure_reality_validation_schema(conn)
    now = int(time.time())
    payload = [
        (
            str(r.get("section")),
            str(r.get("key")),
            r.get("value_real"),
            r.get("value_text"),
            json.dumps(r.get("meta") or {}, default=str),
            now,
        )
        for r in rows
    ]

    def _write(c: Any) -> int:
        c.execute(f"DELETE FROM {TABLE}")
        if payload:
            c.executemany(
                f"""
                INSERT INTO {TABLE}(section, key, value_real, value_text, meta_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return len(payload)

    return research_write_batch(conn, _write)


def _flat_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"section": "summary", "key": "reality_score", "value_real": result.get("reality_score")},
        {"section": "summary", "key": "overfitting_score", "value_real": result.get("overfitting_score")},
        {"section": "summary", "key": "n_trades", "value_real": float(result.get("n_trades") or 0)},
        {
            "section": "summary",
            "key": "largest_weakness",
            "value_text": result.get("largest_weakness"),
            "value_real": None,
        },
        {
            "section": "summary",
            "key": "elapsed_sec",
            "value_real": result.get("elapsed_sec"),
        },
    ]
    ds = result.get("dataset") or {}
    if ds:
        rows.extend([
            {"section": "dataset", "key": "dataset_version", "value_text": ds.get("dataset_version")},
            {"section": "dataset", "key": "lake_rows", "value_real": float(ds.get("lake_rows") or 0)},
            {"section": "dataset", "key": "build_ts", "value_real": float(ds.get("build_ts") or 0) if ds.get("build_ts") else None},
            {"section": "dataset", "key": "hash", "value_text": ds.get("hash")},
            {"section": "dataset", "key": "reality_score", "value_real": result.get("reality_score")},
        ])
    for section in (
        "walk_forward", "oos", "monte_carlo", "stress", "regimes",
        "leave_one_coin", "leave_one_month", "overfit", "reality",
    ):
        blob = result.get(section)
        if blob is None:
            continue
        rows.append({
            "section": section,
            "key": "payload",
            "value_text": "json",
            "meta": blob if isinstance(blob, dict) else {"value": blob},
        })
    fails = result.get("fail_reasons") or []
    for i, fr in enumerate(fails[:12]):
        rows.append({
            "section": "fail_reasons",
            "key": f"rank_{i+1}",
            "value_real": fr.get("severity"),
            "value_text": fr.get("reason"),
            "meta": fr,
        })
    return rows


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fails = result.get("fail_reasons") or []
    lw = result.get("largest_weakness") or "unknown"

    reality_md = "\n".join([
        "# REALITY_REPORT",
        "",
        "_Reality Validation Engine V1 — research only. Attempts to DISPROVE the system._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- n_trades: **{result.get('n_trades')}**",
        f"- source: `{result.get('source')}`",
        f"- reality_score: **{result.get('reality_score')}** / 100",
        f"- overfitting_score: **{result.get('overfitting_score')}** / 100",
        f"- largest_weakness: **{lw}**",
        "",
        "## Walk-Forward",
        f"```json\n{json.dumps({k: (result.get('walk_forward') or {}).get(k) for k in ('ok','n_folds','train_mean_pnl','val_mean_pnl','gap','val_positive_rate')}, indent=2)}\n```",
        "",
        "## Out-of-Sample",
        f"```json\n{json.dumps({k: (result.get('oos') or {}).get(k) for k in ('ok','n_old','n_new','gap_expectancy','gap_sharpe','degradation')}, indent=2)}\n```",
        "",
        "## Monte Carlo",
        f"```json\n{json.dumps({k: (result.get('monte_carlo') or {}).get(k) for k in ('ok','n_sims','fragile','mixed','max_dd_mean')}, indent=2, default=str)}\n```",
        "",
    ])

    overfit_md = "\n".join([
        "# OVERFITTING_REPORT",
        "",
        f"- overfitting_score: **{(result.get('overfit') or {}).get('overfitting_score')}**",
        f"- generalization_gap: **{(result.get('overfit') or {}).get('generalization_gap')}**",
        f"- stability: **{(result.get('overfit') or {}).get('stability')}**",
        f"- confidence: **{(result.get('overfit') or {}).get('confidence')}**",
        "",
        "## Notes",
        *[f"- {n}" for n in ((result.get("overfit") or {}).get("notes") or [])],
        "",
    ])

    robust_md = "\n".join([
        "# ROBUSTNESS_REPORT",
        "",
        "## Stress",
        *[
            f"- {s.get('stress')}: pnl={s.get('pnl')} wr={s.get('wr')} sharpe={s.get('sharpe')}"
            for s in ((result.get("stress") or {}).get("scenarios") or [])
        ],
        "",
        f"survive_rate={(result.get('stress') or {}).get('survive_rate')}",
        "",
        "## Regimes",
        f"```json\n{json.dumps((result.get('regimes') or {}).get('counts'), indent=2)}\n```",
        f"fragile={(result.get('regimes') or {}).get('fragile')}",
        "",
        "## Leave-One-Coin-Out",
        f"sign_flips={(result.get('leave_one_coin') or {}).get('sign_flips')} "
        f"fragile={(result.get('leave_one_coin') or {}).get('fragile')}",
        "",
        "## Leave-One-Month-Out",
        f"sign_flips={(result.get('leave_one_month') or {}).get('sign_flips')} "
        f"fragile={(result.get('leave_one_month') or {}).get('fragile')}",
        "",
    ])

    fail_md = "\n".join([
        "# FAILURE_REPORT",
        "",
        f"Largest weakness: **{lw}**",
        "",
        "## Top fail reasons",
        *[
            f"- [{fr.get('severity')}] `{fr.get('reason')}` ({fr.get('source')})"
            for fr in fails
        ],
        "",
        "_Do not interpret a high reality score as permission to trade live._",
        "",
    ])

    files = {
        "REALITY_REPORT.md": reality_md,
        "OVERFITTING_REPORT.md": overfit_md,
        "ROBUSTNESS_REPORT.md": robust_md,
        "FAILURE_REPORT.md": fail_md,
    }
    paths: dict[str, str] = {}
    for name, text in files.items():
        (BASE_DIR / name).write_text(text, encoding="utf-8")
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        paths[name] = str(BASE_DIR / name)

    slim = {
        k: result.get(k)
        for k in (
            "ok", "elapsed_sec", "n_trades", "source", "reality_score",
            "overfitting_score", "largest_weakness", "fail_reasons",
            "walk_forward", "oos", "monte_carlo", "stress", "regimes",
            "leave_one_coin", "leave_one_month", "overfit", "reality",
        )
    }
    # trim heavy folds
    wf = dict(slim.get("walk_forward") or {})
    if "folds" in wf and isinstance(wf["folds"], list):
        wf["folds"] = wf["folds"][:20]
    slim["walk_forward"] = wf
    jp = OUT_DIR / "reality_validation.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


def format_terminal(result: dict[str, Any]) -> str:
    fails = result.get("fail_reasons") or []
    lines = [
        "REALITY VALIDATION ENGINE V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s n_trades={result.get('n_trades')} source={result.get('source')}",
        "",
        f"Reality score: {result.get('reality_score')} / 100",
        f"Overfitting score: {result.get('overfitting_score')} / 100",
        f"Largest weakness: {result.get('largest_weakness')}",
        "",
        "Top fail reasons",
        *[f"  - {fr.get('reason')} (sev={fr.get('severity')})" for fr in fails[:5]],
        "",
        "research_only=true try_to_disprove=true",
    ]
    return "\n".join(lines)


def run_reality_validation_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
    source: str = "combined",
    mc_sims: int = 10_000,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_reality_validation_schema(conn)
    trades = load_validation_trades(conn, source=source)

    # Also try portfolio sim cache as soft input signal (non-blocking)
    portfolio_meta: dict[str, Any] = {}
    try:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM portfolio_simulations_v1"
        )
        row = cur.fetchone()
        portfolio_meta["n_sims_cached"] = int(row[0] if not hasattr(row, "keys") else row["n"])
    except Exception:
        portfolio_meta["n_sims_cached"] = 0

    wf = walk_forward(trades)
    oos = out_of_sample(trades)
    mc = monte_carlo_reality(trades, n_sims=mc_sims)
    stress = reality_stress(trades)
    regimes = regime_slices(trades)
    loco = leave_one_coin_out(trades)
    lomo = leave_one_month_out(trades)

    parts = {
        "walk_forward": wf,
        "oos": oos,
        "monte_carlo": mc,
        "stress": stress,
        "regimes": regimes,
        "leave_one_coin": loco,
        "leave_one_month": lomo,
    }
    overfit = compute_overfitting_score(parts)
    reality = compute_reality_score(parts, overfit)
    fails = fail_reasons(parts, overfit, reality)
    largest = (fails[0]["reason"] if fails else "none_detected")

    from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
        get_canonical_dataset_meta,
    )
    dataset_meta = get_canonical_dataset_meta(conn)

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": True,
        "research_only": True,
        "execution_unchanged": True,
        "live_unchanged": True,
        "try_to_disprove": True,
        "elapsed_sec": elapsed,
        "n_trades": len(trades),
        "source": source,
        "dataset": dataset_meta,
        "portfolio_meta": portfolio_meta,
        "reality_score": reality.get("reality_score"),
        "overfitting_score": overfit.get("overfitting_score"),
        "largest_weakness": largest,
        "fail_reasons": fails,
        "overfit": overfit,
        "reality": reality,
        **parts,
    }
    result["terminal"] = format_terminal(result)

    if persist:
        persist_reality(conn, rows=_flat_rows(result))
    if write_reports:
        result["paths"] = write_artifacts(result)
    return result


def run_reality_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    """Re-run validation and refresh reports (same as sim — research only)."""
    return run_reality_validation_v1(conn, write_reports=write_reports, persist=True)


__all__ = [
    "SOURCES",
    "format_terminal",
    "load_validation_trades",
    "persist_reality",
    "run_reality_report",
    "run_reality_validation_v1",
    "write_artifacts",
]
