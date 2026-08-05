"""Load trade sets + persist + reports + engine."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_db_session import research_write_lock
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
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.monte_carlo import (
    monte_carlo,
    sensitivity_grid,
    stress_tests,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.schema import (
    EQUITY_TABLE,
    SIM_TABLE,
    ensure_portfolio_sim_schema,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.simulate import (
    ascii_equity,
    portfolio_metrics,
    simulate_equity,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.sizing import (
    CAPITALS,
    parse_risk_specs,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "portfolio_simulator_v1"

MODES = ("production", "decision", "elite")


def load_mode_trades(conn: Any, mode: str) -> list[dict[str, Any]]:
    mode = str(mode).lower()
    if mode == "elite":
        rows = load_stored_candidates(conn, categories=list(STORE_CATEGORIES))
        # need pnl — join journal
        journal = load_journal_rows(conn, book=BOOK_B)
        by_id = {int(r.get("trade_id") or 0): r for r in journal}
        out = []
        for r in rows:
            tid = int(r.get("trade_id") or 0)
            j = by_id.get(tid) or {}
            pnl = r.get("pnl")
            if pnl is None:
                pnl = j.get("pnl")
            if pnl is None:
                continue
            out.append({
                "trade_id": tid,
                "symbol": r.get("symbol") or j.get("symbol"),
                "opened_at": r.get("opened_at") or j.get("opened_at"),
                "direction": r.get("direction") or j.get("direction"),
                "pnl": float(pnl),
                "result": r.get("result") or j.get("result"),
                "mode": "elite",
                "category": r.get("category"),
            })
        out.sort(key=lambda x: int(x.get("opened_at") or 0))
        return out

    book = BOOK_A if mode == "production" else BOOK_B
    rows = load_journal_rows(conn, book=book)
    # accepted only for portfolio of taken trades
    out = []
    for r in rows:
        if int(r.get("accepted") or 0) != 1:
            continue
        if r.get("pnl") is None:
            continue
        out.append({
            "trade_id": int(r.get("trade_id") or 0),
            "symbol": r.get("symbol"),
            "opened_at": r.get("opened_at"),
            "direction": r.get("direction"),
            "pnl": float(r.get("pnl")),
            "result": r.get("result"),
            "mode": mode,
        })
    out.sort(key=lambda x: int(x.get("opened_at") or 0))
    return out


def _rank_score(met: dict[str, Any]) -> float:
    """Higher better: Sharpe primary, soft-cap CAGR, penalize |MaxDD|."""
    sh = float(met.get("sharpe") or 0)
    cagr = min(float(met.get("cagr") or 0), 500.0)
    dd = abs(float(met.get("max_dd") or 0))
    return round(sh * 100.0 + cagr * 0.1 - dd * 2.0, 4)


def persist_sims(
    conn: Any,
    *,
    sims: list[dict[str, Any]],
    equity_by_key: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, int]:
    ensure_portfolio_sim_schema(conn)
    now = int(time.time())
    with research_write_lock():
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            conn.execute(f"DELETE FROM {SIM_TABLE}")
            conn.execute(f"DELETE FROM {EQUITY_TABLE}")
            s_sql = f"""
            INSERT INTO {SIM_TABLE} (
                sim_key, mode, capital, risk_model, risk_param, n_trades,
                pnl, ret_pct, cagr, sharpe, sortino, calmar, pf, wr,
                max_dd, ulcer, mar, recovery, exposure, rank_score, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            s_rows = []
            for s in sims:
                s_rows.append((
                    s.get("sim_key"), s.get("mode"), s.get("capital"),
                    s.get("risk_model"), s.get("risk_param"), s.get("n_trades"),
                    s.get("pnl"), s.get("ret_pct"), s.get("cagr"), s.get("sharpe"),
                    s.get("sortino"), s.get("calmar"), s.get("pf"), s.get("wr"),
                    s.get("max_dd"), s.get("ulcer"), s.get("mar"), s.get("recovery"),
                    s.get("exposure"), s.get("rank_score"),
                    json.dumps(s.get("meta") or {}, ensure_ascii=False), now,
                ))
            if s_rows:
                conn.executemany(s_sql, s_rows)
            e_n = 0
            if equity_by_key:
                e_sql = f"""
                INSERT INTO {EQUITY_TABLE} (sim_key, step, equity, trade_id, pnl)
                VALUES (?,?,?,?,?)
                """
                e_rows = []
                for key, steps in equity_by_key.items():
                    # downsample long curves
                    step_i = max(1, len(steps) // 500)
                    for i, st in enumerate(steps[::step_i]):
                        e_rows.append((
                            key, i, st.get("equity"), st.get("trade_id"), st.get("pnl"),
                        ))
                if e_rows:
                    conn.executemany(e_sql, e_rows)
                    e_n = len(e_rows)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    return {"sims": len(s_rows), "equity_points": e_n}


def format_terminal(result: dict[str, Any]) -> str:
    best = result.get("best") or {}
    lines = [
        "PORTFOLIO SIMULATOR V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s",
        "",
        "Best portfolio",
        f"  {best.get('sim_key')} Sharpe={best.get('sharpe')} "
        f"CAGR={best.get('cagr')} MaxDD={best.get('max_dd')}% "
        f"ret={best.get('ret_pct')}%",
        "",
        "Safest (lowest DD)",
        f"  {(result.get('safest') or {}).get('sim_key')} "
        f"MaxDD={(result.get('safest') or {}).get('max_dd')}%",
        "",
        "Highest Sharpe",
        f"  {(result.get('highest_sharpe') or {}).get('sim_key')} "
        f"Sharpe={(result.get('highest_sharpe') or {}).get('sharpe')}",
        "",
        "Highest CAGR",
        f"  {(result.get('highest_cagr') or {}).get('sim_key')} "
        f"CAGR={(result.get('highest_cagr') or {}).get('cagr')}",
        "",
        "Monte Carlo (best)",
        f"  {(result.get('monte_carlo') or {})}",
        "",
        "Equity (ASCII)",
        result.get("ascii_curve") or "",
        "",
        "research_only=true",
    ]
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    best = result.get("best") or {}
    report = "\n".join([
        "# PORTFOLIO_REPORT",
        "",
        "_Portfolio Simulator V1 — research only._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- best: `{best.get('sim_key')}`",
        f"- Sharpe: {best.get('sharpe')}",
        f"- CAGR: {best.get('cagr')}",
        f"- MaxDD: {best.get('max_dd')}%",
        f"- Return: {best.get('ret_pct')}%",
        "",
        "## Ranked",
        "",
        *[
            f"- {s.get('sim_key')}: Sharpe={s.get('sharpe')} CAGR={s.get('cagr')} "
            f"MaxDD={s.get('max_dd')} rank={s.get('rank_score')}"
            for s in (result.get("sims") or [])[:30]
        ],
        "",
    ])
    eq = "\n".join([
        "# EQUITY_CURVE",
        "",
        "```",
        result.get("ascii_curve") or "",
        "```",
        "",
        f"final={best.get('final_equity')} start={best.get('capital')}",
        "",
    ])
    mc = "\n".join([
        "# MONTE_CARLO",
        "",
        f"```json\n{json.dumps(result.get('monte_carlo'), indent=2)}\n```",
        "",
    ])
    risk = "\n".join([
        "# RISK_REPORT",
        "",
        "## Stress",
        *[
            f"- {s.get('stress')}: pnl={s.get('pnl')} MaxDD={s.get('max_dd')} Sharpe={s.get('sharpe')}"
            for s in (result.get("stress") or [])
        ],
        "",
        "## Sensitivity (top)",
        *[
            f"- cap={s.get('capital')} {s.get('risk_label')}: Sharpe={s.get('sharpe')} "
            f"MaxDD={s.get('max_dd')} CAGR={s.get('cagr')}"
            for s in (result.get("sensitivity") or [])[:20]
        ],
        "",
    ])
    files = {
        "PORTFOLIO_REPORT.md": report,
        "EQUITY_CURVE.md": eq,
        "MONTE_CARLO.md": mc,
        "RISK_REPORT.md": risk,
    }
    paths = {}
    for name, text in files.items():
        (BASE_DIR / name).write_text(text, encoding="utf-8")
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        paths[name] = str(BASE_DIR / name)
    slim = {k: result.get(k) for k in (
        "ok", "elapsed_sec", "best", "safest", "highest_sharpe", "highest_cagr",
        "monte_carlo", "stress", "sims",
    )}
    # trim sims
    slim["sims"] = [
        {k: s.get(k) for k in (
            "sim_key", "mode", "capital", "risk_model", "risk_param",
            "sharpe", "cagr", "max_dd", "ret_pct", "pnl", "rank_score",
        )}
        for s in (result.get("sims") or [])[:100]
    ]
    jp = OUT_DIR / "portfolio_sim.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


def run_portfolio_sim_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
    capital: float = 10000.0,
    mc_sims: int = 1000,
    modes: tuple[str, ...] = MODES,
) -> dict[str, Any]:
    """
    Simulate Production / Decision / Elite portfolios under capital×risk grid.
    Research-only.
    """
    t0 = time.time()
    ensure_portfolio_sim_schema(conn)

    by_mode = {m: load_mode_trades(conn, m) for m in modes}
    if not any(by_mode.values()):
        return {
            "ok": False,
            "error": "no_trades",
            "terminal": (
                "PORTFOLIO SIMULATOR V1\n\n"
                "ERROR no trades — run paper-decision-books / elite-candidates"
            ),
            "research_only": True,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    risk_specs = parse_risk_specs()
    capitals = list(CAPITALS)
    # Always include requested default capital
    if capital not in capitals:
        capitals = sorted(set(capitals + [float(capital)]))

    sims: list[dict[str, Any]] = []
    equity_store: dict[str, list[dict[str, Any]]] = {}
    curves: dict[str, list[float]] = {}

    for mode, trades in by_mode.items():
        if not trades:
            continue
        for cap in capitals:
            for model, param, label in risk_specs:
                key = f"{mode}|c{int(cap)}|{label}"
                store = (cap == float(capital) and mode == "elite" and label == "fixed_2pct")
                sim = simulate_equity(
                    trades,
                    capital=float(cap),
                    risk_model=model,
                    risk_param=float(param),
                    store_curve=store or (cap == float(capital) and label == "fixed_2pct"),
                )
                met = portfolio_metrics(sim, trades, capital=float(cap))
                row = {
                    "sim_key": key,
                    "mode": mode,
                    "capital": float(cap),
                    "risk_model": model,
                    "risk_param": float(param),
                    "risk_label": label,
                    "rank_score": _rank_score(met),
                    "meta": {"n_raw": len(trades)},
                    **met,
                }
                sims.append(row)
                curves[key] = sim.get("equity_curve") or []
                if sim.get("steps"):
                    equity_store[key] = sim["steps"]

    if not sims:
        return {
            "ok": False,
            "error": "empty_sims",
            "terminal": "PORTFOLIO SIMULATOR V1\n\nERROR empty simulations",
            "research_only": True,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    sims.sort(key=lambda r: float(r.get("rank_score") or -1e9), reverse=True)
    best = sims[0]
    safest = min(sims, key=lambda r: abs(float(r.get("max_dd") or 0)))
    highest_sharpe = max(sims, key=lambda r: float(r.get("sharpe") or -1e9))
    highest_cagr = max(sims, key=lambda r: float(r.get("cagr") or -1e9))

    # MC + stress on best mode trades at default capital
    best_mode = str(best.get("mode") or "elite")
    best_trades = by_mode.get(best_mode) or []
    mc = monte_carlo(
        best_trades,
        capital=float(best.get("capital") or capital),
        risk_model=str(best.get("risk_model") or "fixed"),
        risk_param=float(best.get("risk_param") or 0.02),
        n_sims=int(mc_sims),
    )
    stress = stress_tests(
        best_trades,
        capital=float(capital),
        risk_model=str(best.get("risk_model") or "fixed"),
        risk_param=float(best.get("risk_param") or 0.02),
    )
    # Sensitivity for elite (or best mode) around default capital grid already in sims
    sens = [s for s in sims if s.get("mode") == best_mode]
    sens.sort(key=lambda r: float(r.get("sharpe") or -1e9), reverse=True)

    curve = curves.get(str(best.get("sim_key"))) or []
    ascii_c = ascii_equity(curve)

    stored = {"sims": 0, "equity_points": 0}
    if persist:
        stored = persist_sims(conn, sims=sims, equity_by_key=equity_store)

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "sims": sims,
        "best": best,
        "safest": safest,
        "highest_sharpe": highest_sharpe,
        "highest_cagr": highest_cagr,
        "lowest_dd": safest,
        "monte_carlo": mc,
        "stress": stress,
        "sensitivity": sens[:40],
        "ascii_curve": ascii_c,
        "mode_counts": {m: len(v) for m, v in by_mode.items()},
        "stored": stored,
        "elapsed_sec": elapsed,
        "execution_unchanged": True,
        "live_unchanged": True,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        result["paths"] = write_artifacts(result)
    return result


def run_portfolio_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    return run_portfolio_sim_v1(conn, write_reports=write_reports, persist=False, mc_sims=200)


def run_portfolio_compare(conn: Any) -> dict[str, Any]:
    out = run_portfolio_sim_v1(conn, write_reports=False, persist=False, mc_sims=100)
    if not out.get("ok"):
        return out
    lines = ["PORTFOLIO COMPARE V1", ""]
    by_mode: dict[str, list] = {}
    for s in out.get("sims") or []:
        by_mode.setdefault(str(s.get("mode")), []).append(s)
    for mode, rows in by_mode.items():
        top = max(rows, key=lambda r: float(r.get("rank_score") or -1e9))
        lines.append(
            f"{mode}: {top.get('sim_key')} Sharpe={top.get('sharpe')} "
            f"CAGR={top.get('cagr')} MaxDD={top.get('max_dd')}%"
        )
    lines.extend(["", f"elapsed={out.get('elapsed_sec')}s research_only=true"])
    out["terminal"] = "\n".join(lines)
    return out


__all__ = [
    "load_mode_trades",
    "run_portfolio_compare",
    "run_portfolio_report",
    "run_portfolio_sim_v1",
]
