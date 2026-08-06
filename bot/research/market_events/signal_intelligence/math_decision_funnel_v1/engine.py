"""Math Decision Funnel V1 engine — research-only analysis."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_write_manager import research_write_batch
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.metrics import (
    delta_metric,
    stage_metrics,
)
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.schema import (
    FUNNEL_TABLE,
    REJECTIONS_TABLE,
    ensure_decision_funnel_schema,
)
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.stages import (
    FUNNEL_STAGES,
    MODULE_STAGES,
    first_rejector,
    rejecting_modules,
    stage_passes,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import (
    BOOK_D,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.consistency import (
    load_reality_score,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
    load_canonical_elite,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "math_decision_funnel_v1"


def _load_book_d_ids(conn: Any) -> set[int]:
    out: set[int] = set()
    try:
        from bot.research.market_events.signal_intelligence.paper_math_validation_v1.engine import (
            load_math_book_rows,
        )

        for r in load_math_book_rows(conn, book=BOOK_D):
            if int(r.get("accepted") or 0) != 1:
                continue
            tid = int(r.get("trade_id") or 0)
            if tid:
                out.add(tid)
    except Exception:
        pass
    return out


def load_funnel_candidates(conn: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Book A = full candidate universe with counterfactual pnl."""
    rows_a = load_journal_rows(conn, book=BOOK_A)
    rows_b = load_journal_rows(conn, book=BOOK_B, accepted_only=True)
    book_b_ids = {int(r.get("trade_id") or 0) for r in rows_b if int(r.get("trade_id") or 0)}
    book_d_ids = _load_book_d_ids(conn)

    elite = load_canonical_elite(conn)
    elite_ids = {int(e.get("trade_id") or 0) for e in elite if int(e.get("trade_id") or 0)}
    elite_by = {int(e.get("trade_id") or 0): e for e in elite}

    reality = load_reality_score(conn)
    reality_score = reality.get("reality_score")

    candidates: list[dict[str, Any]] = []
    for r in rows_a:
        tid = int(r.get("trade_id") or 0)
        if not tid:
            continue
        row = dict(r)
        em = elite_by.get(tid) or {}
        if not row.get("decision_rank") and em.get("category"):
            row["elite_category"] = em.get("category")
            row["decision_rank"] = em.get("category")
        if row.get("pnl") is None and em.get("pnl") is not None:
            row["pnl"] = em.get("pnl")
        candidates.append(row)

    ctx = {
        "reality_score": reality_score,
        "elite_ids": elite_ids,
        "book_b_ids": book_b_ids,
        "book_d_ids": book_d_ids,
    }
    return candidates, ctx


def build_waterfall(
    candidates: list[dict[str, Any]],
    *,
    ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """Sequential Decision Waterfall — survivors shrink stage by stage."""
    survivors = list(candidates)
    stages_out: list[dict[str, Any]] = []
    for i, stage in enumerate(FUNNEL_STAGES):
        n_in = len(survivors)
        if stage == "Candidate":
            passed = list(survivors)
        else:
            passed = [r for r in survivors if stage_passes(stage, r, ctx=ctx)]
        met = stage_metrics(passed, input_n=n_in)
        stages_out.append({
            "stage": stage,
            "stage_order": i,
            **met,
        })
        survivors = passed
    return stages_out


def build_rejections(
    candidates: list[dict[str, Any]],
    *,
    ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rejection attribution for every trade that fails any module stage."""
    now = int(time.time())
    out: list[dict[str, Any]] = []
    for r in candidates:
        mods = rejecting_modules(r, ctx=ctx)
        if not mods:
            continue
        pnl = r.get("pnl")
        try:
            pnl_f = float(pnl) if pnl is not None else None
        except Exception:
            pnl_f = None
        recoverable = 1 if pnl_f is not None and pnl_f > 0 else 0
        lost_ev = float(pnl_f) if recoverable else 0.0
        conf = r.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else None
        except Exception:
            conf_f = None
        out.append({
            "trade_id": int(r.get("trade_id") or 0),
            "symbol": r.get("symbol"),
            "opened_at": r.get("opened_at"),
            "first_rejector": mods[0],
            "all_rejectors": mods,
            "all_rejectors_json": json.dumps(mods),
            "confidence": conf_f,
            "historical_wr": r.get("historical_wr"),
            "historical_ev": r.get("historical_ev"),
            "pnl": pnl_f,
            "recoverable": recoverable,
            "lost_ev": lost_ev,
            "meta_json": json.dumps({"research_only": True}),
            "updated_at": now,
        })
    return out


def top_rejectors(rejections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    c: Counter[str] = Counter()
    for r in rejections:
        c[str(r.get("first_rejector") or "UNKNOWN")] += 1
    return [{"module": m, "rejected": n} for m, n in c.most_common()]


def recoverable_by_module(rejections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lost EV for rejected-but-profitable trades, attributed to first rejector."""
    by: dict[str, dict[str, Any]] = defaultdict(lambda: {"module": "", "n": 0, "lost_ev": 0.0})
    for r in rejections:
        if int(r.get("recoverable") or 0) != 1:
            continue
        mod = str(r.get("first_rejector") or "UNKNOWN")
        by[mod]["module"] = mod
        by[mod]["n"] += 1
        by[mod]["lost_ev"] = round(float(by[mod]["lost_ev"]) + float(r.get("lost_ev") or 0), 6)
    rows = list(by.values())
    rows.sort(key=lambda x: float(x.get("lost_ev") or 0), reverse=True)
    return rows


def module_influence(
    candidates: list[dict[str, Any]],
    *,
    ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    For each module: metrics of trades that pass vs fail that module alone
    (independent of waterfall order) → ΔWR / ΔEV / ΔPF / ΔSharpe.
    """
    base = stage_metrics(candidates)
    out: list[dict[str, Any]] = []
    for stage in MODULE_STAGES:
        passed = [r for r in candidates if stage_passes(stage, r, ctx=ctx)]
        met = stage_metrics(passed)
        out.append({
            "module": stage,
            "n_pass": met["accepted_n"],
            "n_fail": len(candidates) - met["accepted_n"],
            "wr": met.get("wr"),
            "ev": met.get("ev"),
            "pf": met.get("pf"),
            "sharpe": met.get("sharpe"),
            "delta_wr": delta_metric(base.get("wr"), met.get("wr")),
            "delta_ev": delta_metric(base.get("ev"), met.get("ev")),
            "delta_pf": delta_metric(base.get("pf"), met.get("pf")),
            "delta_sharpe": delta_metric(base.get("sharpe"), met.get("sharpe")),
        })
    return out


def persist_funnel(
    conn: Any,
    *,
    stages: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
) -> dict[str, int]:
    ensure_decision_funnel_schema(conn)
    now = int(time.time())

    stage_rows = [
        (
            s["stage"],
            int(s["stage_order"]),
            int(s["input_n"]),
            int(s["accepted_n"]),
            int(s["rejected_n"]),
            s.get("acceptance_pct"),
            s.get("wr"),
            s.get("pf"),
            s.get("ev"),
            s.get("sharpe"),
            json.dumps({"research_only": True}),
            now,
        )
        for s in stages
    ]
    rej_rows = [
        (
            int(r["trade_id"]),
            r.get("symbol"),
            r.get("opened_at"),
            str(r["first_rejector"]),
            r.get("all_rejectors_json") or json.dumps(r.get("all_rejectors") or []),
            r.get("confidence"),
            r.get("historical_wr"),
            r.get("historical_ev"),
            r.get("pnl"),
            int(r.get("recoverable") or 0),
            r.get("lost_ev"),
            r.get("meta_json") or "{}",
            int(r.get("updated_at") or now),
        )
        for r in rejections
    ]

    def _write(c: Any) -> dict[str, int]:
        c.execute(f"DELETE FROM {FUNNEL_TABLE}")
        c.execute(f"DELETE FROM {REJECTIONS_TABLE}")
        if stage_rows:
            c.executemany(
                f"""
                INSERT INTO {FUNNEL_TABLE}(
                    stage, stage_order, input_n, accepted_n, rejected_n,
                    acceptance_pct, wr, pf, ev, sharpe, meta_json, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                stage_rows,
            )
        if rej_rows:
            c.executemany(
                f"""
                INSERT INTO {REJECTIONS_TABLE}(
                    trade_id, symbol, opened_at, first_rejector, all_rejectors_json,
                    confidence, historical_wr, historical_ev, pnl, recoverable,
                    lost_ev, meta_json, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rej_rows,
            )
        return {"stages": len(stage_rows), "rejections": len(rej_rows)}

    return research_write_batch(conn, _write)


def write_funnel_reports(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stages = result.get("waterfall") or []
    rejectors = result.get("top_rejectors") or []
    recoverable = result.get("recoverable") or []
    influence = result.get("module_influence") or []

    funnel_lines = [
        "# DECISION_FUNNEL",
        "",
        "_Math Decision Funnel V1 — research only. Observe Gate/Strategy/Decision/Elite._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- candidates: **{result.get('n_candidates')}**",
        f"- largest rejector: **{result.get('largest_rejector')}**",
        f"- largest recoverable EV module: **{result.get('largest_recoverable_module')}** "
        f"({result.get('largest_recoverable_ev')})",
        "",
        "## Stages",
        "",
        "| Stage | Input | Accepted | Rejected | Acc% | WR | PF | EV | Sharpe |",
        "|-------|------:|---------:|---------:|-----:|---:|---:|---:|-------:|",
    ]
    for s in stages:
        funnel_lines.append(
            f"| {s['stage']} | {s['input_n']} | {s['accepted_n']} | {s['rejected_n']} | "
            f"{s.get('acceptance_pct')} | {s.get('wr')} | {s.get('pf')} | {s.get('ev')} | {s.get('sharpe')} |"
        )
    funnel_lines += [
        "",
        "## Module Influence",
        "",
        "| Module | n_pass | ΔWR | ΔEV | ΔPF | ΔSharpe |",
        "|--------|-------:|----:|----:|----:|--------:|",
    ]
    for m in influence:
        funnel_lines.append(
            f"| {m['module']} | {m['n_pass']} | {m.get('delta_wr')} | {m.get('delta_ev')} | "
            f"{m.get('delta_pf')} | {m.get('delta_sharpe')} |"
        )
    funnel_text = "\n".join(funnel_lines) + "\n"

    waterfall_lines = [
        "# DECISION_WATERFALL",
        "",
        f"{result.get('n_candidates')} candidates",
        "",
    ]
    for s in stages:
        waterfall_lines.append(f"↓")
        waterfall_lines.append(f"{s['stage']}")
        waterfall_lines.append(f"{s['accepted_n']}")
        waterfall_lines.append("")
    waterfall_text = "\n".join(waterfall_lines)

    rej_lines = [
        "# TOP_REJECTORS",
        "",
        "| Module | Rejected |",
        "|--------|---------:|",
    ]
    for r in rejectors:
        rej_lines.append(f"| {r['module']} | {r['rejected']} |")
    rej_text = "\n".join(rej_lines) + "\n"

    rec_lines = [
        "# RECOVERABLE_EV",
        "",
        "_Rejected trades that later showed positive PnL (Book A counterfactual)._",
        "",
        f"- total recoverable: **{result.get('n_recoverable')}**",
        f"- total lost EV: **{result.get('total_lost_ev')}**",
        "",
        "| Module | n | lost_ev |",
        "|--------|--:|--------:|",
    ]
    for r in recoverable:
        rec_lines.append(f"| {r['module']} | {r['n']} | {r['lost_ev']} |")
    rec_text = "\n".join(rec_lines) + "\n"

    paths = {
        "DECISION_FUNNEL.md": str(BASE_DIR / "DECISION_FUNNEL.md"),
        "DECISION_WATERFALL.md": str(BASE_DIR / "DECISION_WATERFALL.md"),
        "TOP_REJECTORS.md": str(BASE_DIR / "TOP_REJECTORS.md"),
        "RECOVERABLE_EV.md": str(BASE_DIR / "RECOVERABLE_EV.md"),
    }
    (BASE_DIR / "DECISION_FUNNEL.md").write_text(funnel_text, encoding="utf-8")
    (BASE_DIR / "DECISION_WATERFALL.md").write_text(waterfall_text, encoding="utf-8")
    (BASE_DIR / "TOP_REJECTORS.md").write_text(rej_text, encoding="utf-8")
    (BASE_DIR / "RECOVERABLE_EV.md").write_text(rec_text, encoding="utf-8")
    for name, text in (
        ("DECISION_FUNNEL.md", funnel_text),
        ("DECISION_WATERFALL.md", waterfall_text),
        ("TOP_REJECTORS.md", rej_text),
        ("RECOVERABLE_EV.md", rec_text),
    ):
        (OUT_DIR / name).write_text(text, encoding="utf-8")
    jp = OUT_DIR / "decision_funnel.json"
    slim = {
        k: result.get(k)
        for k in (
            "ok", "elapsed_sec", "n_candidates", "waterfall", "top_rejectors",
            "recoverable", "module_influence", "largest_rejector",
            "largest_recoverable_module", "largest_recoverable_ev",
            "n_recoverable", "total_lost_ev",
        )
    }
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


def format_terminal(result: dict[str, Any], *, mode: str = "funnel") -> str:
    if mode == "waterfall":
        lines = ["DECISION WATERFALL V1", ""]
        for s in result.get("waterfall") or []:
            lines.append(f"{s['stage']}: {s['accepted_n']} / {s['input_n']}")
        return "\n".join(lines)
    if mode == "rejectors":
        lines = ["TOP REJECTORS V1", ""]
        for r in result.get("top_rejectors") or []:
            lines.append(f"{r['module']}\trejected\t{r['rejected']}")
        return "\n".join(lines)
    return "\n".join([
        "MATH DECISION FUNNEL V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s candidates={result.get('n_candidates')}",
        f"largest_rejector={result.get('largest_rejector')}",
        f"largest_recoverable_ev={result.get('largest_recoverable_module')} "
        f"({result.get('largest_recoverable_ev')})",
        f"n_recoverable={result.get('n_recoverable')} total_lost_ev={result.get('total_lost_ev')}",
        "",
        "research_only=true observe_only=true",
    ])


def run_decision_funnel_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
    mode: str = "funnel",
) -> dict[str, Any]:
    t0 = time.time()
    ensure_decision_funnel_schema(conn)
    candidates, ctx = load_funnel_candidates(conn)
    waterfall = build_waterfall(candidates, ctx=ctx)
    rejections = build_rejections(candidates, ctx=ctx)
    rejectors = top_rejectors(rejections)
    recoverable = recoverable_by_module(rejections)
    influence = module_influence(candidates, ctx=ctx)

    largest_rejector = rejectors[0]["module"] if rejectors else None
    largest_rec_mod = recoverable[0]["module"] if recoverable else None
    largest_rec_ev = recoverable[0]["lost_ev"] if recoverable else 0.0
    n_rec = sum(1 for r in rejections if int(r.get("recoverable") or 0) == 1)
    total_lost = round(sum(float(r.get("lost_ev") or 0) for r in rejections if int(r.get("recoverable") or 0) == 1), 6)

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": True,
        "research_only": True,
        "observe_only": True,
        "execution_unchanged": True,
        "elapsed_sec": elapsed,
        "n_candidates": len(candidates),
        "waterfall": waterfall,
        "top_rejectors": rejectors,
        "recoverable": recoverable,
        "module_influence": influence,
        "largest_rejector": largest_rejector,
        "largest_recoverable_module": largest_rec_mod,
        "largest_recoverable_ev": largest_rec_ev,
        "n_recoverable": n_rec,
        "total_lost_ev": total_lost,
        "reality_score": ctx.get("reality_score"),
        "n_elite": len(ctx.get("elite_ids") or set()),
        "n_book_b": len(ctx.get("book_b_ids") or set()),
        "n_book_d": len(ctx.get("book_d_ids") or set()),
    }
    result["terminal"] = format_terminal(result, mode=mode)

    if persist:
        result["persisted"] = persist_funnel(conn, stages=waterfall, rejections=rejections)
    if write_reports:
        result["paths"] = write_funnel_reports(result)
    return result


def run_decision_waterfall(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return run_decision_funnel_v1(conn, mode="waterfall", **kwargs)


def run_decision_rejectors(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return run_decision_funnel_v1(conn, mode="rejectors", **kwargs)


__all__ = [
    "build_rejections",
    "build_waterfall",
    "format_terminal",
    "load_funnel_candidates",
    "module_influence",
    "persist_funnel",
    "recoverable_by_module",
    "run_decision_funnel_v1",
    "run_decision_rejectors",
    "run_decision_waterfall",
    "top_rejectors",
    "write_funnel_reports",
]
