"""Deduplicate conditions and minimize rule sets without new features."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import DNARule
from bot.research.market_events.signal_intelligence.trading_rules_v1.parse import (
    canonicalize,
    is_local_condition,
    rule_from_setup,
    split_conditions,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.validate import (
    MIN_N_READY,
    validate_rule,
)


def _cond_key(conds: list[str]) -> tuple[str, ...]:
    return tuple(sorted(canonicalize(c) for c in conds))


def dedupe_setups(setups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate condition sets (order-insensitive)."""
    seen: set[tuple[str, ...]] = set()
    out = []
    for s in setups:
        parsed = rule_from_setup(str(s.get("setup") or ""))
        key = _cond_key(parsed["conditions"])
        if not key or key in seen:
            continue
        # strip local-only setups early for READY path
        seen.add(key)
        out.append({**s, **parsed})
    return out


def minimize_conditions(
    rows: list[dict[str, Any]],
    conditions: list[str],
    *,
    atomics: list[DNARule],
    raw_conditions: list[str] | None = None,
    min_n: int = MIN_N_READY,
    max_pf_drop: float = 0.15,
) -> dict[str, Any] | None:
    """
    Greedy backward elimination: drop a condition if PF stays within max_pf_drop
    relative and EV / CI quality hold.
    """
    raw = list(raw_conditions or conditions)
    conds = [canonicalize(c) for c in conditions]
    # align raw to conds length
    if len(raw) != len(conds):
        raw = list(conds)

    base = validate_rule(
        rows, conds, atomics=atomics, raw_conditions=raw, min_n=min_n, kind="ready"
    )
    if base is None:
        # try without ready filter — still minimize for candidates
        base = validate_rule(
            rows, conds, atomics=atomics, raw_conditions=raw, min_n=max(50, min_n // 5), kind="ready"
        )
        if base is None:
            return None

    def _pf(v: dict[str, Any]) -> float:
        if v.get("pf") is None and v.get("pf_inf"):
            return 50.0
        return float(v.get("pf") or 0.0)

    base_pf = _pf(base)
    changed = True
    while changed and len(conds) > 1:
        changed = False
        best_drop = None
        best_val = None
        for i in range(len(conds)):
            trial = conds[:i] + conds[i + 1 :]
            trial_raw = raw[:i] + raw[i + 1 :]
            # don't leave only local conditions
            if trial and all(is_local_condition(c) for c in trial):
                continue
            val = validate_rule(
                rows, trial, atomics=atomics, raw_conditions=trial_raw,
                min_n=min_n, kind="ready",
            )
            if val is None:
                continue
            if float(val.get("ev") or 0) <= 0:
                continue
            if _pf(val) < base_pf * (1.0 - max_pf_drop):
                continue
            # prefer fewer conditions, then higher PF
            score = (_pf(val), float(val.get("ev") or 0), -len(trial))
            if best_val is None or score > (
                _pf(best_val), float(best_val.get("ev") or 0), -len(best_val["conditions"])
            ):
                best_drop = i
                best_val = val
        if best_drop is not None and best_val is not None:
            conds = best_val["conditions"]
            raw = list(conds)  # labels already canonical / matched
            base = best_val
            base_pf = _pf(base)
            changed = True

    return base


def extract_candidates(
    rows: list[dict[str, Any]],
    profitable: list[dict[str, Any]],
    *,
    atomics: list[DNARule],
    min_n: int = MIN_N_READY,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Dedupe → drop local-only → minimize → validate READY."""
    deduped = dedupe_setups(profitable)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for s in deduped:
        conds = [c for c in s.get("conditions") or [] if not is_local_condition(c)]
        raw = [c for c in (s.get("raw_conditions") or []) if not is_local_condition(c)]
        if not conds:
            continue
        # drop Pattern=g31 noise if ubiquitous
        conds2, raw2 = [], []
        for c, r in zip(conds, raw if len(raw) == len(conds) else conds):
            if str(c).startswith("Pattern=g31") or str(r).startswith("Pattern=g31"):
                continue
            conds2.append(c)
            raw2.append(r)
        if not conds2:
            continue
        minimized = minimize_conditions(
            rows, conds2, atomics=atomics, raw_conditions=raw2, min_n=min_n
        )
        if minimized is None or not minimized.get("ready_ok"):
            continue
        key = _cond_key(minimized["conditions"])
        if key in seen:
            continue
        seen.add(key)
        out.append(minimized)
        if len(out) >= limit:
            break
    # also try known strong DNA cores explicitly
    cores = [
        ["EMA20>EMA50", "ADX>22.5", "MACD>0", "ATR<0.6"],
        ["EMA20>EMA50", "ADX>22.5", "MACD>0"],
        ["ADX>22.5", "MACD>0"],
        ["ADX>22.5", "EMA20>EMA50"],
        ["ATR<0.6", "ADX>22.5", "MACD>0"],
        ["EMA20>EMA50", "Confidence>5.68"],
        ["ATR<0.6", "EMA20>EMA50"],
        ["MACD>0", "EMA20>EMA50"],
    ]
    # map display → DNA labels
    alias = {
        "MACD>0": "MACD+",
        "MACD<0": "MACD-",
        "ATR<0.6": "ATR%<0.6",
        "ADX>22.5": "ADX>22.5",
        "EMA20>EMA50": "EMA20>EMA50",
    }
    # ADX label may be dynamic — find closest from atomics
    adx_labels = [r.label for r in atomics if r.label.startswith("ADX>")]
    atr_labels = [r.label for r in atomics if r.label.startswith("ATR%<") and " " not in r.label]
    conf_labels = [r.label for r in atomics if r.label.startswith("Confidence>")]

    def _resolve(c: str) -> str:
        if c == "ADX>22.5" and adx_labels:
            return adx_labels[0]
        if c == "ATR<0.6" and atr_labels:
            # prefer exact 0.6 threshold label
            for a in atr_labels:
                if "0.6" in a:
                    return a
            return atr_labels[0]
        if c.startswith("Confidence>") and conf_labels:
            return conf_labels[0]
        return alias.get(c, c)

    for core in cores:
        raw = [_resolve(c) for c in core]
        conds = [canonicalize(c) for c in raw]
        key = _cond_key(conds)
        if key in seen:
            continue
        minimized = minimize_conditions(
            rows, conds, atomics=atomics, raw_conditions=raw, min_n=min_n
        )
        if minimized is None or not minimized.get("ready_ok"):
            # accept core as-is if validates (keep full pack when it passes)
            val = validate_rule(
                rows, conds, atomics=atomics, raw_conditions=raw, min_n=min_n, kind="ready"
            )
            if val is None or not val.get("ready_ok"):
                continue
            minimized = val
        else:
            # Also keep the unminimized core if it passes and is longer
            full = validate_rule(
                rows, conds, atomics=atomics, raw_conditions=raw, min_n=min_n, kind="ready"
            )
            if full and full.get("ready_ok") and len(full["conditions"]) > len(minimized["conditions"]):
                key_full = _cond_key(full["conditions"])
                if key_full not in seen:
                    seen.add(key_full)
                    out.append(full)
        key = _cond_key(minimized["conditions"])
        if key in seen:
            continue
        seen.add(key)
        out.append(minimized)

    out.sort(
        key=lambda r: (
            len(r.get("conditions") or []),  # prefer richer minimal packs first… overridden below
            float(r["pf"]) if r.get("pf") is not None else 50.0,
            float(r.get("ev") or 0),
            int(r.get("n") or 0),
            float(r.get("ci_lo") or 0),
        ),
        reverse=True,
    )
    # Final filter: ≥2 conditions, no direction-only, no pure local leftovers
    filtered = []
    for r in out:
        conds = r.get("conditions") or []
        if len(conds) < 2:
            continue
        if len(conds) == 1 and str(conds[0]) in ("LONG", "SHORT"):
            continue
        if all(c in ("LONG", "SHORT") for c in conds):
            continue
        filtered.append(r)
    # Rank by PF, EV, n (quality), keep packs with 2–4 conditions
    filtered.sort(
        key=lambda r: (
            float(r["pf"]) if r.get("pf") is not None else 50.0,
            float(r.get("ev") or 0),
            int(r.get("n") or 0),
            float(r.get("ci_lo") or 0),
        ),
        reverse=True,
    )
    return filtered


def extract_blocks(
    rows: list[dict[str, Any]],
    losing: list[dict[str, Any]],
    *,
    atomics: list[DNARule],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Minimize losing setups into HARD BLOCK candidates."""
    deduped = dedupe_setups(losing)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    gate_rules = [r for r in atomics if r.label.startswith("Gate=")]
    for gr in gate_rules:
        val = validate_rule(
            rows, [canonicalize(gr.label)], atomics=atomics,
            raw_conditions=[gr.label], min_n=40, kind="block",
        )
        if val and val.get("block_ok"):
            key = _cond_key(val["conditions"])
            if key not in seen:
                seen.add(key)
                out.append(val)

    for s in deduped:
        conds = list(s.get("conditions") or [])
        raw = list(s.get("raw_conditions") or conds)
        stripped = [
            (c, r) for c, r in zip(conds, raw)
            if not is_local_condition(c)
            and not str(c).startswith("Pattern=g31")
            and not str(r).startswith("Pattern=g31")
        ]
        if stripped:
            conds = [c for c, _ in stripped]
            raw = [r for _, r in stripped]
        if not conds:
            continue
        # Sparse lake fields (funding/regime/oi) only exist on ~50 rows — not transferable blocks
        if any(str(c).startswith(("Funding", "Regime=", "OI")) for c in conds):
            continue
        if any(str(c) in ("LONG", "SHORT") for c in conds) and not any(
            str(c).startswith("Gate=") for c in conds
        ):
            # direction-only combos with sparse macros already skipped; drop LONG/SHORT fillers
            conds2 = [c for c in conds if c not in ("LONG", "SHORT")]
            raw2 = [r for c, r in zip(conds, raw) if c not in ("LONG", "SHORT")]
            if not conds2:
                continue
            conds, raw = conds2, raw2

        best = validate_rule(
            rows, conds, atomics=atomics, raw_conditions=raw, min_n=40, kind="block"
        )
        if best is None or not best.get("block_ok"):
            continue
        is_gate = any(str(c).startswith("Gate=") for c in best["conditions"])
        if any(str(c).startswith(("Funding", "Regime=", "OI")) for c in best["conditions"]):
            continue
        if not is_gate and int(best.get("n") or 0) < 200:
            continue
        cur_c, cur_r = list(best["conditions"]), list(best["conditions"])
        # try shrink using raw labels via atomics (canonical ok)
        improved = True
        while improved and len(cur_c) > 1:
            improved = False
            for i in range(len(cur_c)):
                trial_c = cur_c[:i] + cur_c[i + 1 :]
                if any(str(c).startswith(("Funding", "Regime=", "OI")) for c in trial_c):
                    continue
                val = validate_rule(
                    rows, trial_c, atomics=atomics, raw_conditions=trial_c,
                    min_n=40, kind="block",
                )
                if val and val.get("block_ok") and float(val.get("ev") or 0) < 0:
                    if float(val.get("pf") or 1) <= 0.70:
                        best = val
                        cur_c = trial_c
                        improved = True
                        break
        if any(str(c).startswith(("Funding", "Regime=", "OI")) for c in best["conditions"]):
            continue
        key = _cond_key(best["conditions"])
        if key in seen:
            continue
        seen.add(key)
        out.append(best)
        if len(out) >= limit:
            break

    # Final safety filter
    out = [
        b for b in out
        if not any(str(c).startswith(("Funding", "Regime=", "OI")) for c in (b.get("conditions") or []))
    ]
    out.sort(key=lambda r: (float(r.get("pf") or 0), float(r.get("ev") or 0), -int(r.get("n") or 0)))
    return out


__all__ = [
    "dedupe_setups",
    "extract_blocks",
    "extract_candidates",
    "minimize_conditions",
]
