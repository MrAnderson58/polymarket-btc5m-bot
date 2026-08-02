"""Setup mining: atomic + combination rules → profitable / losing DNA."""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    pf_sort_key,
    trade_metrics,
)

Predicate = Callable[[dict[str, Any]], bool]

MIN_N_PROFIT = 25
MIN_N_LOSS = 25


@dataclass(frozen=True)
class DNARule:
    id: str
    label: str
    features: tuple[str, ...]
    pred: Predicate


def _atomic_rules(rows: list[dict[str, Any]]) -> list[DNARule]:
    rules: list[DNARule] = []

    # RSI fixed
    for name, lo, hi, label in (
        ("rsi_lt_30", 0.0, 30.0, "RSI<30"),
        ("rsi_lt_34", 0.0, 34.0, "RSI<34"),
        ("rsi_30_45", 30.0, 45.0, "RSI 30-45"),
        ("rsi_45_55", 45.0, 55.0, "RSI 45-55"),
        ("rsi_55_70", 55.0, 70.0, "RSI 55-70"),
        ("rsi_gt_70", 70.0, 100.1, "RSI>70"),
    ):
        rules.append(DNARule(
            id=name, label=label, features=("rsi",),
            pred=lambda r, lo=lo, hi=hi: (_safe_float(r.get("rsi")) is not None and lo <= float(r["rsi"]) < hi),
        ))

    # ATR% bins via quantiles
    atr_vals = [float(r["atr_pct"]) for r in rows if r.get("atr_pct") is not None]
    if len(atr_vals) >= 50:
        q33, q66 = float(np.quantile(atr_vals, 0.33)), float(np.quantile(atr_vals, 0.66))
        for name, lo, hi, label in (
            ("atr_low", -1e9, q33, f"ATR%<{q33:.3g}"),
            ("atr_mid", q33, q66, f"ATR% {q33:.3g}-{q66:.3g}"),
            ("atr_high", q66, 1e9, f"ATR%>{q66:.3g}"),
        ):
            rules.append(DNARule(
                id=name, label=label, features=("atr_pct",),
                pred=lambda r, lo=lo, hi=hi: (
                    _safe_float(r.get("atr_pct")) is not None and lo <= float(r["atr_pct"]) < hi
                ),
            ))
        # absolute-ish low ATR like example ATR<0.6 if units match median scale
        med = float(np.median(atr_vals))
        thr = 0.6 if med < 5 else med * 0.5
        rules.append(DNARule(
            id="atr_lt_thr", label=f"ATR%<{thr:.3g}", features=("atr_pct",),
            pred=lambda r, t=thr: (_safe_float(r.get("atr_pct")) is not None and float(r["atr_pct"]) < t),
        ))

    # ADX
    adx_vals = [float(r["adx"]) for r in rows if r.get("adx") is not None]
    if len(adx_vals) >= 50:
        med = float(np.median(adx_vals))
        rules.append(DNARule(
            id="adx_high", label=f"ADX>{med:.3g}", features=("adx",),
            pred=lambda r, t=med: (_safe_float(r.get("adx")) is not None and float(r["adx"]) > t),
        ))
        rules.append(DNARule(
            id="adx_low", label=f"ADX<={med:.3g}", features=("adx",),
            pred=lambda r, t=med: (_safe_float(r.get("adx")) is not None and float(r["adx"]) <= t),
        ))

    # MACD sign
    rules.append(DNARule(
        id="macd_pos", label="MACD+", features=("macd",),
        pred=lambda r: (_safe_float(r.get("macd")) is not None and float(r["macd"]) > 0),
    ))
    rules.append(DNARule(
        id="macd_neg", label="MACD-", features=("macd",),
        pred=lambda r: (_safe_float(r.get("macd")) is not None and float(r["macd"]) < 0),
    ))

    # Funding / OI signs
    for feat, label_pos, label_neg in (
        ("funding_sign", "Funding+", "Funding-"),
        ("oi_sign", "OI+", "OI-"),
    ):
        rules.append(DNARule(
            id=f"{feat}_pos", label=label_pos, features=(feat,),
            pred=lambda r, f=feat: str(r.get(f)) == "+",
        ))
        rules.append(DNARule(
            id=f"{feat}_neg", label=label_neg, features=(feat,),
            pred=lambda r, f=feat: str(r.get(f)) == "-",
        ))

    # Direction
    for d in ("LONG", "SHORT"):
        rules.append(DNARule(
            id=f"dir_{d}", label=d, features=("direction",),
            pred=lambda r, d=d: str(r.get("direction") or "").upper() == d,
        ))

    # Hour buckets
    for h in range(24):
        n_h = sum(1 for r in rows if r.get("hour") == h)
        if n_h < 40:
            continue
        rules.append(DNARule(
            id=f"hour_{h}", label=f"Hour={h}", features=("hour",),
            pred=lambda r, h=h: r.get("hour") == h,
        ))

    # Weekday
    wd_names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    for w in range(7):
        rules.append(DNARule(
            id=f"wd_{w}", label=f"Weekday={wd_names[w]}", features=("weekday",),
            pred=lambda r, w=w: r.get("weekday") == w,
        ))

    # Top symbols
    top_syms = [s for s, n in Counter(str(r.get("symbol")) for r in rows).most_common(20) if n >= 40]
    for s in top_syms:
        rules.append(DNARule(
            id=f"sym_{s}", label=f"Coin={s}", features=("symbol",),
            pred=lambda r, s=s: str(r.get("symbol")) == s,
        ))

    # Regime / gate / pattern (top) — skip UNK noise
    for feat, prefix in (("regime", "Regime"), ("gate", "Gate"), ("pattern", "Pattern")):
        counts = Counter(
            str(r.get(feat))
            for r in rows
            if r.get(feat) and str(r.get(feat)) not in ("UNK", "None", "nested")
        )
        for val, n in counts.most_common(8):
            if n < 30:
                continue
            rules.append(DNARule(
                id=f"{feat}_{val}", label=f"{prefix}={val}", features=(feat,),
                pred=lambda r, f=feat, v=val: str(r.get(f)) == v,
            ))

    # Confidence high/low
    conf = [float(r["confidence"]) for r in rows if r.get("confidence") is not None]
    if len(conf) >= 50:
        med = float(np.median(conf))
        rules.append(DNARule(
            id="conf_high", label=f"Confidence>{med:.3g}", features=("confidence",),
            pred=lambda r, t=med: (_safe_float(r.get("confidence")) is not None and float(r["confidence"]) > t),
        ))
        rules.append(DNARule(
            id="conf_low", label=f"Confidence<={med:.3g}", features=("confidence",),
            pred=lambda r, t=med: (_safe_float(r.get("confidence")) is not None and float(r["confidence"]) <= t),
        ))

    # EMA stack
    rules.append(DNARule(
        id="ema_bull", label="EMA20>EMA50", features=("ema20", "ema50"),
        pred=lambda r: (
            _safe_float(r.get("ema20")) is not None
            and _safe_float(r.get("ema50")) is not None
            and float(r["ema20"]) > float(r["ema50"])
        ),
    ))
    rules.append(DNARule(
        id="ema_bear", label="EMA20<EMA50", features=("ema20", "ema50"),
        pred=lambda r: (
            _safe_float(r.get("ema20")) is not None
            and _safe_float(r.get("ema50")) is not None
            and float(r["ema20"]) < float(r["ema50"])
        ),
    ))

    # News
    news = [float(r["news_score"]) for r in rows if r.get("news_score") is not None]
    if len(news) >= 40:
        med = float(np.median(news))
        rules.append(DNARule(
            id="news_pos", label=f"News>{med:.3g}", features=("news_score",),
            pred=lambda r, t=med: (_safe_float(r.get("news_score")) is not None and float(r["news_score"]) > t),
        ))

    return rules


def _score_mask(rows: list[dict[str, Any]], mask: list[bool]) -> dict[str, Any] | None:
    pnls = [float(rows[i]["pnl"]) for i, m in enumerate(mask) if m]
    if len(pnls) < 5:
        return None
    return trade_metrics(pnls)


def mine_setups(
    rows: list[dict[str, Any]],
    *,
    top_n: int = 100,
    min_n: int = MIN_N_PROFIT,
    max_combo: int = 3,
) -> dict[str, Any]:
    """Mine atomic + 2/3-way combination setups."""
    atomics = _atomic_rules(rows)
    # Precompute masks
    masks: dict[str, np.ndarray] = {}
    for rule in atomics:
        masks[rule.id] = np.array([bool(rule.pred(r)) for r in rows], dtype=bool)

    scored: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    def _add(label: str, features: tuple[str, ...], mask: np.ndarray, kind: str) -> None:
        if label in seen_labels:
            return
        n = int(mask.sum())
        if n < min_n:
            return
        m = trade_metrics([float(rows[i]["pnl"]) for i in np.nonzero(mask)[0]])
        # Skip all-flat books (e.g. MATIC/TON zero PnL corpus artifacts)
        if int(m.get("n_nonzero") or 0) < max(10, min_n // 3):
            return
        if float(m.get("ev") or 0) == 0.0 and float(m.get("total") or 0) == 0.0:
            return
        row = {
            "setup": label,
            "features": list(features),
            "kind": kind,
            **m,
        }
        seen_labels.add(label)
        scored.append(row)

    for rule in atomics:
        _add(rule.label, rule.features, masks[rule.id], "atomic")

    # Combinations of compatible atomics (different feature families)
    # Limit candidates: take top atomics by |ev|*sqrt(n)
    atomic_scored = []
    for rule in atomics:
        m = masks[rule.id]
        n = int(m.sum())
        if n < min_n:
            continue
        met = trade_metrics([float(rows[i]["pnl"]) for i in np.nonzero(m)[0]])
        atomic_scored.append((rule, met, abs(float(met.get("ev") or 0)) * (n ** 0.5)))
    atomic_scored.sort(key=lambda x: -x[2])
    pool = [a[0] for a in atomic_scored[:40]]

    for k in range(2, max_combo + 1):
        for combo in itertools.combinations(pool, k):
            feats = set()
            ok = True
            for r in combo:
                # avoid combining two rules on same primary feature
                primary = r.features[0]
                if primary in feats and primary not in ("ema20", "ema50"):
                    ok = False
                    break
                feats.add(primary)
            if not ok:
                continue
            mask = masks[combo[0].id].copy()
            for r in combo[1:]:
                mask &= masks[r.id]
            label = " + ".join(r.label for r in combo)
            feat_t = tuple(dict.fromkeys(f for r in combo for f in r.features))
            _add(label, feat_t, mask, f"combo{k}")

    profitable = [
        s for s in scored
        if ((s.get("pf") is None and s.get("pf_inf")) or (s.get("pf") or 0) >= 1.2)
        and float(s.get("ev") or 0) > 0
        and int(s.get("n_wins") or 0) >= 5
    ]
    losing = [
        s for s in scored
        if s.get("pf") is not None
        and float(s["pf"]) <= 0.85
        and float(s.get("ev") or 0) < 0
        and int(s.get("n_losses") or 0) >= 5
    ]

    profitable.sort(key=pf_sort_key, reverse=True)
    losing.sort(key=lambda s: (float(s.get("pf") or 0), float(s.get("ev") or 0), -int(s.get("n") or 0)))

    return {
        "n_atomics": len(atomics),
        "n_scored": len(scored),
        "profitable": profitable[:top_n],
        "losing": losing[:top_n],
        "all_scored": scored,
        "atomic_rules": atomics,
        "masks": masks,
    }


__all__ = ["DNARule", "mine_setups"]
