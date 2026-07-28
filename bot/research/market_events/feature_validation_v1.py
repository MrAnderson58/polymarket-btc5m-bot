"""Feature Validation V1 — which features improve EV (research only, no gate changes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bot.research.market_events.expectancy_intelligence.stats import (
    median,
    trade_outcome_stats,
)
from bot.research.market_events.research_pack_01.historical import load_closed_s55_trades

MIN_FILTER_N = 10
MIN_RELIABLE_N = 30

FeatureKey = str
Predicate = Callable[[dict[str, Any]], bool]


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    s = trade_outcome_stats(trades, min_reliable_n=MIN_RELIABLE_N)
    pf = s["profit_factor"]
    return {
        "n": s["trades"],
        "ev": s["expectancy"],
        "pf": None if pf is None else (None if pf == float("inf") else float(pf)),
        "wr": s["win_rate"],
        "reliable": s["reliable"],
    }


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 4)


def _median_split(
    trades: list[dict[str, Any]], key: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float | None]:
    vals = [float(t[key]) for t in trades if isinstance(t.get(key), (int, float))]
    med = median(vals)
    if med is None:
        return [], [], None
    lo = [t for t in trades if isinstance(t.get(key), (int, float)) and float(t[key]) <= med]
    hi = [t for t in trades if isinstance(t.get(key), (int, float)) and float(t[key]) > med]
    return lo, hi, float(med)


def _best_half_filter(
    trades: list[dict[str, Any]], key: str, label: str
) -> tuple[Predicate | None, str, dict[str, Any]]:
    lo, hi, med = _median_split(trades, key)
    if not lo and not hi:
        return None, f"{label}: no values", {"side": None, "median": None}
    m_lo = _metrics(lo)
    m_hi = _metrics(hi)
    if m_hi["n"] >= m_lo["n"] and m_hi["ev"] >= m_lo["ev"]:
        side, subset, thr = "high", hi, med
    elif m_lo["ev"] > m_hi["ev"]:
        side, subset, thr = "low", lo, med
    else:
        side, subset, thr = ("high" if m_hi["n"] >= m_lo["n"] else "low"), (hi if m_hi["n"] >= m_lo["n"] else lo), med
    if side == "high":
        pred: Predicate = lambda t, k=key, m=thr: isinstance(t.get(k), (int, float)) and float(t[k]) > float(m)  # type: ignore[arg-type]
        rule = f"{label} > median ({thr})"
    else:
        pred = lambda t, k=key, m=thr: isinstance(t.get(k), (int, float)) and float(t[k]) <= float(m)  # type: ignore[arg-type]
        rule = f"{label} ≤ median ({thr})"
    return pred, rule, {"side": side, "median": thr, "n_pass": len(subset)}


def _direction_filter(trades: list[dict[str, Any]]) -> tuple[Predicate | None, str, dict[str, Any]]:
    longs = [t for t in trades if str(t.get("direction") or "").upper() == "LONG"]
    shorts = [t for t in trades if str(t.get("direction") or "").upper() == "SHORT"]
    if not longs and not shorts:
        return None, "Direction: empty", {}
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    if longs:
        candidates.append(("LONG", longs))
    if shorts:
        candidates.append(("SHORT", shorts))
    best_name, best_subset = max(candidates, key=lambda x: (_metrics(x[1])["ev"], len(x[1])))
    return (
        lambda t, name=best_name: str(t.get("direction") or "").upper() == name,
        f"Direction = {best_name}",
        {"side": best_name},
    )


def _regime_filter(trades: list[dict[str, Any]]) -> tuple[Predicate | None, str, dict[str, Any]]:
    by_reg: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        r = t.get("market_regime")
        if r is None or not str(r).strip():
            continue
        by_reg.setdefault(str(r).strip().upper(), []).append(t)
    if not by_reg:
        return None, "Regime: empty", {}
    best_name = None
    best_ev = -1e18
    for name, subset in by_reg.items():
        m = _metrics(subset)
        if m["n"] < MIN_FILTER_N:
            continue
        if m["ev"] > best_ev:
            best_ev = m["ev"]
            best_name = name
    if best_name is None:
        # fall back to largest regime
        best_name = max(by_reg.items(), key=lambda x: len(x[1]))[0]
    return (
        lambda t, name=best_name: str(t.get("market_regime") or "").strip().upper() == name,
        f"Regime = {best_name}",
        {"side": best_name},
    )


def _neighbor_ev_filter(trades: list[dict[str, Any]]) -> tuple[Predicate | None, str, dict[str, Any]]:
    """Favor trades where neighbor EV at entry was non-negative (gate estimate)."""
    with_v = [t for t in trades if t.get("neighbor_ev") is not None]
    if len(with_v) < MIN_FILTER_N:
        return None, "Neighbor EV: sparse", {}
    return (
        lambda t: t.get("neighbor_ev") is not None and float(t["neighbor_ev"]) >= 0.0,
        "Neighbor EV ≥ 0",
        {"side": "ge0"},
    )


FEATURE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("funding", "Funding", "numeric"),
    ("trend", "Trend", "numeric"),
    ("oi_delta", "OI", "numeric"),
    ("volatility", "Volatility", "numeric"),
    ("ai_score", "AI Score", "numeric"),
    ("fear_greed", "Fear & Greed", "numeric"),
    ("neighbor_ev", "Neighbor EV", "neighbor"),
    ("market_regime", "Regime", "regime"),
    ("direction", "Direction", "direction"),
)


def build_feature_predicates(
    trades: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, label, kind in FEATURE_SPECS:
        if kind == "numeric":
            pred, rule, meta = _best_half_filter(trades, key, label)
        elif kind == "neighbor":
            pred, rule, meta = _neighbor_ev_filter(trades)
        elif kind == "regime":
            pred, rule, meta = _regime_filter(trades)
        else:
            pred, rule, meta = _direction_filter(trades)
        out[key] = {"label": label, "predicate": pred, "rule": rule, "meta": meta}
    return out


def single_feature_effects(
    trades: list[dict[str, Any]],
    predicates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    base = _metrics(trades)
    rows: list[dict[str, Any]] = []
    for key, spec in predicates.items():
        pred = spec["predicate"]
        if pred is None:
            rows.append(
                {
                    "feature": spec["label"],
                    "key": key,
                    "rule": spec["rule"],
                    "baseline": base,
                    "filtered": _metrics([]),
                    "delta_ev": None,
                    "delta_pf": None,
                    "delta_wr": None,
                    "status": "no_data",
                }
            )
            continue
        subset = [t for t in trades if pred(t)]
        filt = _metrics(subset)
        rows.append(
            {
                "feature": spec["label"],
                "key": key,
                "rule": spec["rule"],
                "baseline": base,
                "filtered": filt,
                "delta_ev": _delta(filt["ev"], base["ev"]),
                "delta_pf": _delta(filt["pf"], base["pf"]),
                "delta_wr": _delta(filt["wr"], base["wr"]),
                "status": "ok" if filt["n"] >= MIN_FILTER_N else "low_n",
            }
        )
    return rows


def leave_one_out(
    trades: list[dict[str, Any]],
    predicates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    active = {k: v for k, v in predicates.items() if v["predicate"] is not None}
    if not active:
        return []

    def apply_all(except_key: str | None = None) -> list[dict[str, Any]]:
        out = trades
        for k, spec in active.items():
            if except_key is not None and k == except_key:
                continue
            pred = spec["predicate"]
            assert pred is not None
            out = [t for t in out if pred(t)]
        return out

    full = apply_all(None)
    full_m = _metrics(full)
    rows = [
        {
            "dropped": "(none — all filters)",
            "key": None,
            "metrics": full_m,
            "delta_ev_vs_full": 0.0,
            "delta_pf_vs_full": 0.0,
            "delta_wr_vs_full": 0.0,
            "delta_n_vs_full": 0,
        }
    ]
    for key, spec in active.items():
        without = apply_all(key)
        m = _metrics(without)
        rows.append(
            {
                "dropped": spec["label"],
                "key": key,
                "metrics": m,
                "delta_ev_vs_full": _delta(m["ev"], full_m["ev"]),
                "delta_pf_vs_full": _delta(m["pf"], full_m["pf"]),
                "delta_wr_vs_full": _delta(m["wr"], full_m["wr"]),
                "delta_n_vs_full": m["n"] - full_m["n"],
            }
        )
    return rows


def interaction_matrix(
    trades: list[dict[str, Any]],
    predicates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    keys = [k for k, v in predicates.items() if v["predicate"] is not None]
    single_ev: dict[str, float] = {}
    for k in keys:
        pred = predicates[k]["predicate"]
        assert pred is not None
        single_ev[k] = _metrics([t for t in trades if pred(t)])["ev"]

    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            pa, pb = predicates[a]["predicate"], predicates[b]["predicate"]
            assert pa is not None and pb is not None
            both = [t for t in trades if pa(t) and pb(t)]
            m = _metrics(both)
            best_single = max(single_ev[a], single_ev[b])
            synergy = _delta(m["ev"], best_single)
            pairs.append(
                {
                    "a": predicates[a]["label"],
                    "b": predicates[b]["label"],
                    "key_a": a,
                    "key_b": b,
                    "n": m["n"],
                    "ev": m["ev"],
                    "ev_a": single_ev[a],
                    "ev_b": single_ev[b],
                    "synergy": synergy,
                    "stronger_together": synergy is not None and synergy > 0.05 and m["n"] >= MIN_FILTER_N,
                }
            )
    pairs.sort(key=lambda r: (-(r["synergy"] or -1e9), -r["n"]))
    return pairs


def stability_check(
    trades: list[dict[str, Any]],
    predicates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(
        trades,
        key=lambda t: (t.get("closed_at") is None, t.get("closed_at") or 0),
    )
    n = len(ordered)
    if n < 2 * MIN_FILTER_N:
        return [
            {
                "feature": predicates[k]["label"],
                "key": k,
                "stable": False,
                "reason": "sample too small for split",
                "delta_ev_h1": None,
                "delta_ev_h2": None,
            }
            for k in predicates
        ]
    mid = n // 2
    h1, h2 = ordered[:mid], ordered[mid:]
    base1, base2 = _metrics(h1), _metrics(h2)
    rows: list[dict[str, Any]] = []
    for key, spec in predicates.items():
        pred = spec["predicate"]
        if pred is None:
            rows.append(
                {
                    "feature": spec["label"],
                    "key": key,
                    "stable": False,
                    "reason": "no predicate",
                    "delta_ev_h1": None,
                    "delta_ev_h2": None,
                }
            )
            continue
        d1 = _delta(_metrics([t for t in h1 if pred(t)])["ev"], base1["ev"])
        d2 = _delta(_metrics([t for t in h2 if pred(t)])["ev"], base2["ev"])
        stable = True
        reason = "sign consistent"
        if d1 is None or d2 is None:
            stable = False
            reason = "missing half"
        elif (d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0):
            stable = False
            reason = "ΔEV sign flipped across halves"
        elif abs(d1 - d2) > max(1.0, abs(d1) + abs(d2)):
            stable = False
            reason = "ΔEV magnitude unstable"
        rows.append(
            {
                "feature": spec["label"],
                "key": key,
                "stable": stable,
                "reason": reason,
                "delta_ev_h1": d1,
                "delta_ev_h2": d2,
            }
        )
    return rows


def rank_features(
    singles: list[dict[str, Any]],
    loo: list[dict[str, Any]],
    stability: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    loo_by_key = {r["key"]: r for r in loo if r.get("key")}
    stab_by_key = {r["key"]: r for r in stability}

    ranked: list[dict[str, Any]] = []
    for s in singles:
        key = s["key"]
        stab = stab_by_key.get(key) or {}
        lo = loo_by_key.get(key) or {}
        # Value of filter: positive ΔEV when applied; LOO: if dropping worsens EV (ΔEV vs full < 0 when dropped means filter was helping? 
        # When we DROP filter, metrics are without that filter. If delta_ev_vs_full > 0, removing filter improved EV → filter hurt.
        # If delta_ev_vs_full < 0, removing filter hurt EV → filter helped.
        loo_help = None
        if lo.get("delta_ev_vs_full") is not None:
            loo_help = round(-float(lo["delta_ev_vs_full"]), 4)  # positive = filter adds value

        n = int(s["filtered"]["n"] or 0)
        d_ev = s.get("delta_ev")
        d_pf = s.get("delta_pf")
        stable = bool(stab.get("stable"))
        sample_score = min(1.0, n / 100.0) if n else 0.0
        trust = 0.0
        trust += 0.35 * (1.0 if (d_ev is not None and d_ev > 0) else 0.0)
        trust += 0.20 * (1.0 if (d_pf is not None and d_pf > 0) else 0.0)
        trust += 0.25 * (1.0 if stable else 0.0)
        trust += 0.20 * sample_score
        if loo_help is not None and loo_help > 0:
            trust = min(1.0, trust + 0.1)

        score = round(100.0 * trust, 1)
        verdict = "REMOVE"
        if s.get("status") == "no_data" or n < MIN_FILTER_N:
            verdict = "WATCH"
            score = min(score, 35.0)
        elif d_ev is not None and d_ev > 0.15 and stable and n >= MIN_RELIABLE_N:
            verdict = "KEEP"
        elif d_ev is not None and d_ev > 0 and (stable or n >= MIN_FILTER_N):
            verdict = "WATCH"
        elif d_ev is not None and d_ev <= 0:
            verdict = "REMOVE" if n >= MIN_FILTER_N else "WATCH"

        ranked.append(
            {
                "feature": s["feature"],
                "key": key,
                "score": score,
                "verdict": verdict,
                "delta_ev": d_ev,
                "delta_pf": d_pf,
                "delta_wr": s.get("delta_wr"),
                "n": n,
                "stable": stable,
                "stability_reason": stab.get("reason"),
                "loo_value": loo_help,
                "rule": s.get("rule"),
                "ev_influence": d_ev,
                "pf_influence": d_pf,
                "trust": round(trust, 3),
            }
        )
    ranked.sort(key=lambda r: -r["score"])
    return ranked


def build_feature_validation(conn: Any, *, limit: int = 50000) -> dict[str, Any]:
    trades = load_closed_s55_trades(conn, limit=limit)
    predicates = build_feature_predicates(trades)
    singles = single_feature_effects(trades, predicates)
    loo = leave_one_out(trades, predicates)
    interactions = interaction_matrix(trades, predicates)
    stability = stability_check(trades, predicates)
    ranking = rank_features(singles, loo, stability)
    keep = [r for r in ranking if r["verdict"] == "KEEP"]
    watch = [r for r in ranking if r["verdict"] == "WATCH"]
    remove = [r for r in ranking if r["verdict"] == "REMOVE"]
    return {
        "n_trades": len(trades),
        "baseline": _metrics(trades),
        "singles": singles,
        "leave_one_out": loo,
        "interactions": interactions,
        "stability": stability,
        "ranking": ranking,
        "keep": keep,
        "watch": watch,
        "remove": remove,
    }


def write_feature_validation_report(data: dict[str, Any], root: Path | None = None) -> Path:
    out_dir = root or Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "feature_validation.md"
    base = data["baseline"]
    lines = [
        "# Feature Validation V1",
        "",
        f"Closed S55 trades: **{data['n_trades']}**",
        f"Baseline: EV={base['ev']}%  PF={base['pf']}  WR={base['wr']}%  n={base['n']}",
        "",
        "_Research only — does not change live gates._",
        "",
        "## KEEP",
        "",
    ]
    if not data["keep"]:
        lines.append("_None — need more stable positive ΔEV (or larger n)._")
    for r in data["keep"]:
        lines.append(
            f"- **{r['feature']}** score={r['score']}  ΔEV={r['delta_ev']}  "
            f"ΔPF={r['delta_pf']}  n={r['n']}  stable={r['stable']}  rule=`{r['rule']}`"
        )
    lines.extend(["", "## WATCH", ""])
    if not data["watch"]:
        lines.append("_None_")
    for r in data["watch"]:
        lines.append(
            f"- **{r['feature']}** score={r['score']}  ΔEV={r['delta_ev']}  "
            f"n={r['n']}  stable={r['stable']}  ({r.get('stability_reason')})"
        )
    lines.extend(["", "## REMOVE", ""])
    if not data["remove"]:
        lines.append("_None_")
    for r in data["remove"]:
        lines.append(
            f"- **{r['feature']}** score={r['score']}  ΔEV={r['delta_ev']}  "
            f"n={r['n']}  — negative or unreliable effect"
        )

    lines.extend(["", "## 1. Single Feature Effect", ""])
    for s in data["singles"]:
        f = s["filtered"]
        b = s["baseline"]
        lines.append(
            f"### {s['feature']}\n"
            f"- Rule: `{s['rule']}`\n"
            f"- Without filter: EV={b['ev']} PF={b['pf']} WR={b['wr']}% n={b['n']}\n"
            f"- With filter: EV={f['ev']} PF={f['pf']} WR={f['wr']}% n={f['n']}\n"
            f"- ΔEV={s['delta_ev']}  ΔPF={s['delta_pf']}  ΔWR={s['delta_wr']}  status={s['status']}\n"
        )

    lines.extend(["", "## 2. Leave-One-Out", ""])
    for r in data["leave_one_out"]:
        m = r["metrics"]
        lines.append(
            f"- Drop **{r['dropped']}**: EV={m['ev']} PF={m['pf']} WR={m['wr']}% n={m['n']}  "
            f"ΔEV={r['delta_ev_vs_full']} Δn={r['delta_n_vs_full']}"
        )

    lines.extend(["", "## 3. Interaction Matrix (top by synergy)", ""])
    for p in data["interactions"][:15]:
        flag = " STRONGER_TOGETHER" if p["stronger_together"] else ""
        lines.append(
            f"- {p['a']} × {p['b']}: EV={p['ev']} (A={p['ev_a']}, B={p['ev_b']}) "
            f"synergy={p['synergy']} n={p['n']}{flag}"
        )

    lines.extend(["", "## 4. Stability (1st vs 2nd half)", ""])
    for s in data["stability"]:
        mark = "stable" if s["stable"] else "UNSTABLE"
        lines.append(
            f"- {s['feature']}: {mark} — H1 ΔEV={s['delta_ev_h1']} H2 ΔEV={s['delta_ev_h2']} ({s['reason']})"
        )

    lines.extend(["", "## 5. Ranking", ""])
    lines.append("| Feature | Score | Verdict | ΔEV | ΔPF | n | Stable | Trust |")
    lines.append("|---------|------:|---------|-----|-----|---|:------:|------:|")
    for r in data["ranking"]:
        lines.append(
            f"| {r['feature']} | {r['score']} | {r['verdict']} | {r['delta_ev']} | "
            f"{r['delta_pf']} | {r['n']} | {r['stable']} | {r['trust']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def format_feature_validation_cli(data: dict[str, Any]) -> str:
    lines = [
        "FEATURE VALIDATION V1 (research only)",
        f"  n_trades={data['n_trades']}  baseline_EV={data['baseline']['ev']}%  "
        f"PF={data['baseline']['pf']}  WR={data['baseline']['wr']}%",
        "",
        "KEEP:",
    ]
    for r in data["keep"] or [{"feature": "(none)", "score": 0, "delta_ev": None, "n": 0}]:
        if r.get("feature") == "(none)":
            lines.append("  (none)")
        else:
            lines.append(f"  + {r['feature']} score={r['score']} ΔEV={r['delta_ev']} n={r['n']}")
    lines.append("WATCH:")
    for r in data["watch"]:
        lines.append(f"  ~ {r['feature']} score={r['score']} ΔEV={r['delta_ev']} n={r['n']}")
    if not data["watch"]:
        lines.append("  (none)")
    lines.append("REMOVE:")
    for r in data["remove"]:
        lines.append(f"  - {r['feature']} score={r['score']} ΔEV={r['delta_ev']} n={r['n']}")
    if not data["remove"]:
        lines.append("  (none)")
    lines.append("")
    lines.append("Top interactions:")
    for p in data["interactions"][:5]:
        lines.append(f"  {p['a']}×{p['b']} synergy={p['synergy']} n={p['n']}")
    return "\n".join(lines)


def run_feature_validation(conn: Any, *, write_reports: bool = True) -> str:
    data = build_feature_validation(conn)
    path = None
    knowledge_path = None
    sync_stats: dict[str, int] = {}
    if write_reports:
        path = write_feature_validation_report(data)
        try:
            from bot.research.market_events.knowledge_engine.report import (
                write_knowledge_report,
            )
            from bot.research.market_events.knowledge_engine.store import (
                upsert_from_feature_validation,
            )

            sync_stats = upsert_from_feature_validation(conn, data)
            knowledge_path = write_knowledge_report(conn)
        except Exception as exc:
            sync_stats = {"error": str(exc)}  # type: ignore[dict-item]
    text = format_feature_validation_cli(data)
    if path:
        text += f"\n\nWrote {path}"
    if knowledge_path:
        text += f"\nUpdated Knowledge DB → {knowledge_path} ({sync_stats})"
    elif sync_stats.get("error"):
        text += f"\nKnowledge sync failed: {sync_stats['error']}"
    return text
