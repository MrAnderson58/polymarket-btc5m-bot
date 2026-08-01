"""Recurring replay pattern discovery."""

from __future__ import annotations

from collections import Counter
from typing import Any


def discover_replay_patterns(replays: list[dict[str, Any]], *, top_n: int = 20) -> list[dict[str, Any]]:
    """Mine recurring (regime, direction, gate, alpha) + liquidity signatures."""
    counter: Counter[str] = Counter()
    examples: dict[str, list[int]] = {}
    for r in replays:
        feats = r.get("features") or {}
        liq = r.get("liquidity") or {}
        key = "|".join([
            str(feats.get("regime") or r.get("regime") or ""),
            str(feats.get("direction") or ""),
            str(feats.get("gate_decision") or ""),
            str(feats.get("alpha_cluster") or "") or "-",
            "vol+" if (liq.get("volume_expansion") or 0) > 0.05 else "vol-",
            "oi+" if (liq.get("oi_expansion") or 0) > 0.05 else "oi-",
        ])
        counter[key] += 1
        examples.setdefault(key, []).append(int(r.get("trade_id") or 0))

    out = []
    for key, n in counter.most_common(top_n):
        parts = key.split("|")
        out.append({
            "pattern": key,
            "count": n,
            "regime": parts[0] if len(parts) > 0 else "",
            "direction": parts[1] if len(parts) > 1 else "",
            "gate": parts[2] if len(parts) > 2 else "",
            "alpha_cluster": parts[3] if len(parts) > 3 else "",
            "liquidity_sig": "|".join(parts[4:]) if len(parts) > 4 else "",
            "example_trade_ids": [x for x in examples.get(key, []) if x][:10],
        })
    return out


def missing_data_report(replays: list[dict[str, Any]]) -> dict[str, Any]:
    field_counts: Counter[str] = Counter()
    total = max(1, len(replays))
    frame_missing: Counter[str] = Counter()
    for r in replays:
        miss = r.get("missing_report") or {}
        for f, n in (miss.get("fields") or {}).items():
            field_counts[str(f)] += int(n)
        for f in miss.get("entry_missing") or []:
            frame_missing[str(f)] += 1
    return {
        "n_replays": len(replays),
        "fields_missing_frequency": {
            k: {"count": v, "share": round(v / total, 4)}
            for k, v in field_counts.most_common(40)
        },
        "entry_field_missing_share": {
            k: round(v / total, 4) for k, v in frame_missing.most_common(40)
        },
        "mean_quality": round(
            sum(float(r.get("quality") or 0) for r in replays) / total, 4
        ),
        "coverage_pct": round(
            100.0 * sum(1 for r in replays if float(r.get("quality") or 0) >= 0.5) / total, 2
        ),
    }


__all__ = ["discover_replay_patterns", "missing_data_report"]
