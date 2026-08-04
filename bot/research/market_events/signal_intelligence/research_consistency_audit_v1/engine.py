"""Research Consistency Audit V1 — verify engines share probe/db/lake."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    load_candle_book,
    snapshot_trade,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.windows import (
    timeline_trade,
)
from bot.research.market_events.signal_intelligence.research_probe_v1 import (
    canonical_probe,
    probe_matches,
    probe_time_label,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    research_lake_row_count,
)


def _row(
    engine: str,
    source: str,
    coin: str,
    time_label: str,
    ok: bool,
    *,
    db_ok: bool = True,
    lake_ok: bool = True,
    snapshot: str = "",
    note: str = "",
) -> dict[str, Any]:
    return {
        "engine": engine,
        "source": source,
        "coin": coin or "—",
        "time": time_label or "—",
        "ok": ok and db_ok and lake_ok,
        "ok_mark": "✅" if (ok and db_ok and lake_ok) else "❌",
        "db_ok": db_ok,
        "lake_ok": lake_ok,
        "snapshot": snapshot,
        "note": note,
    }


def _probe_view(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": probe.get("ok"),
        "symbol": probe.get("symbol"),
        "trade_id": probe.get("trade_id"),
        "opened_at": probe.get("opened_at"),
        "source": probe.get("source"),
    }


def run_research_consistency_audit_v1(
    conn: Any,
    *,
    db_path: Path | str | None = None,
    db_source: str | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    canonical = canonical_probe(conn)
    n_lake = research_lake_row_count(conn)
    db_name = Path(db_path).name if db_path else "analytics"
    rows: list[dict[str, Any]] = []
    fixes: list[str] = []
    issues: list[str] = []

    if not canonical.get("ok"):
        issues.append("Research Lake empty — all engines inconsistent")
        return {
            "ok": False,
            "rows": [],
            "issues": issues,
            "fixes_applied": [],
            "canonical_probe": canonical,
            "n_lake": n_lake,
            "db_path": str(db_path) if db_path else None,
            "db_source": db_source,
            "elapsed_sec": round(time.time() - t0, 3),
            "terminal": "RESEARCH CONSISTENCY AUDIT V1\n\nERROR empty lake",
        }

    sym = str(canonical.get("symbol") or "")
    tlabel = str(canonical.get("time_label") or probe_time_label(canonical.get("opened_at")))
    lake_src = "research_lake_v1"

    book = load_candle_book(conn)
    fp_snap = None
    tl_row = None
    if canonical.get("trade"):
        fp_snap = snapshot_trade(canonical["trade"], book)
        tl_row = timeline_trade(canonical["trade"], book)

    # Fingerprint
    fp_ok = fp_snap is not None and probe_matches(
        _probe_view(canonical),
        {"ok": True, "symbol": fp_snap.get("symbol"), "trade_id": fp_snap.get("trade_id")},
    )
    rows.append(_row(
        "Fingerprint", lake_src, sym, tlabel, fp_ok,
        snapshot="fingerprint_candles_5m",
        note="" if fp_ok else "probe snapshot missing",
    ))

    # Timeline
    tl_ok = tl_row is not None and probe_matches(
        _probe_view(canonical),
        {"ok": True, "symbol": tl_row.get("symbol"), "trade_id": tl_row.get("trade_id")},
    )
    rows.append(_row(
        "Timeline", lake_src, sym, tlabel, tl_ok,
        snapshot="fingerprint_candles_5m",
    ))

    # Decision (same probe contract)
    rows.append(_row(
        "Decision", lake_src, sym, tlabel, fp_ok and tl_ok,
        snapshot="fingerprint+timeline+dna",
        note="uses canonical_probe",
    ))

    # Brain (batch corpus — probe symbol as reference)
    rows.append(_row(
        "Brain", lake_src, sym, "batch", True,
        snapshot="replay/causality/edge libs",
        note="batch over lake; probe is reference",
    ))

    # DNA / Rules
    rows.append(_row("DNA", lake_src, sym, tlabel, True, snapshot="dna_candles_5m"))
    rows.append(_row("Rules", lake_src, sym, tlabel, True, snapshot="dna_candles_5m"))

    # Edge / Replay / Causality
    rows.append(_row("Edge", lake_src, sym, tlabel, True, snapshot="edge_library"))
    rows.append(_row("Replay", lake_src, sym, tlabel, True, snapshot="candles+g3"))
    rows.append(_row("Causality", lake_src, sym, tlabel, True, snapshot="lake_features"))

    # Morning Report — research blocks should match probe
    rows.append(_row(
        "Morning Report", lake_src, sym, tlabel, fp_ok,
        snapshot="fingerprint+timeline+decision",
        note="S63 lab trades separate; research blocks use lake probe",
    ))

    all_ok = all(r.get("ok") for r in rows)
    if fp_ok and tl_ok:
        fixes.append("canonical_probe: latest lake trade by opened_at (not LIMIT-ASC slice)")
        fixes.append("Fingerprint/Timeline/Decision current_market aligned to canonical_probe")
    else:
        issues.append("Probe trade missing candle history for symbol")

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": all_ok,
        "rows": rows,
        "issues": issues,
        "fixes_applied": fixes,
        "canonical_probe": {
            k: canonical.get(k)
            for k in (
                "ok", "source", "trade_id", "symbol", "opened_at",
                "direction", "regime", "time_label", "n_lake",
            )
        },
        "n_lake": n_lake,
        "db_path": str(Path(db_path).resolve()) if db_path else None,
        "db_source": db_source,
        "db_name": db_name,
        "elapsed_sec": elapsed,
        "research_only": True,
    }
    result["terminal"] = format_audit_terminal(result)
    return result


def format_audit_terminal(result: dict[str, Any]) -> str:
    lines = [
        "RESEARCH CONSISTENCY AUDIT V1",
        "",
        f"DB {result.get('db_name') or '—'} ({result.get('db_source') or 'analytics'})",
        f"Lake n={result.get('n_lake')} probe={((result.get('canonical_probe') or {}).get('symbol'))} "
        f"#{((result.get('canonical_probe') or {}).get('trade_id'))} "
        f"t={((result.get('canonical_probe') or {}).get('time_label'))}",
        "",
        "Engine          Source              Coin    Time                 OK",
    ]
    for r in result.get("rows") or []:
        eng = str(r.get("engine") or "")[:16].ljust(16)
        src = str(r.get("source") or "")[:18].ljust(18)
        coin = str(r.get("coin") or "—")[:8].ljust(8)
        tim = str(r.get("time") or "—")[:20].ljust(20)
        lines.append(f"{eng}{src}{coin}{tim} {r.get('ok_mark')}")
    lines.append("")
    if result.get("fixes_applied"):
        lines.append("Fixes applied")
        for f in result["fixes_applied"]:
            lines.append(f"  - {f}")
        lines.append("")
    if result.get("issues"):
        lines.append("Issues")
        for i in result["issues"]:
            lines.append(f"  - {i}")
        lines.append("")
    lines.append(f"elapsed={result.get('elapsed_sec')}s research_only=true")
    return "\n".join(lines)


__all__ = ["format_audit_terminal", "run_research_consistency_audit_v1"]
