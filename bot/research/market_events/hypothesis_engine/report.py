"""hypothesis-show CLI and reports/research/hypotheses.md."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.market_events.hypothesis_engine.generate import MIN_SAMPLE_FLAG
from bot.research.market_events.hypothesis_engine.schema import (
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_TESTING,
    STATUS_VALIDATED,
    ensure_hypothesis_engine_schema,
)
from bot.research.market_events.hypothesis_engine.store import load_hypothesis_bundle


def _group(bundle: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "top": sorted(bundle, key=lambda h: (-float(h.get("priority") or 0), -float(h.get("confidence") or 0)))[:15],
        STATUS_NEW: [h for h in bundle if h.get("status") == STATUS_NEW],
        STATUS_TESTING: [h for h in bundle if h.get("status") == STATUS_TESTING],
        STATUS_VALIDATED: [h for h in bundle if h.get("status") == STATUS_VALIDATED],
        STATUS_REJECTED: [h for h in bundle if h.get("status") == STATUS_REJECTED],
        "LOW_SAMPLE": [h for h in bundle if int(h.get("sample_size") or 0) < MIN_SAMPLE_FLAG],
    }


def _fmt_hyp_line(h: dict[str, Any]) -> str:
    return (
        f"#{h.get('id')} [{h.get('status')}] {h.get('title')}  "
        f"P={h.get('priority')} C={h.get('confidence')}% "
        f"E={h.get('evidence_score')} n={h.get('sample_size')} "
        f"({h.get('generated_from')})"
    )


def format_hypothesis_detail(h: dict[str, Any]) -> str:
    lines = [
        f"Hypothesis #{h.get('id')}",
        "",
        "Название",
        str(h.get("title") or ""),
        "",
        "Основание",
    ]
    for ev in h.get("evidence") or []:
        lines.append(
            f"  {ev.get('source_type')}  {ev.get('source_name')}  {ev.get('reference')}"
        )
    if not h.get("evidence"):
        lines.append("  (no evidence — invalid)")
    lines.extend(
        [
            "",
            "Размер выборки",
            str(h.get("sample_size")),
            "",
            "Evidence Score",
            str(h.get("evidence_score")),
            "",
            "Confidence",
            f"{h.get('confidence')}%",
            "",
            "Priority",
            str(h.get("priority")),
            "",
            "Status",
            str(h.get("status")),
            "",
            "Description",
            str(h.get("description") or ""),
        ]
    )
    return "\n".join(lines)


def format_hypothesis_show(conn: Any) -> str:
    ensure_hypothesis_engine_schema(conn)
    bundle = load_hypothesis_bundle(conn)
    g = _group(bundle)
    lines = [
        "RESEARCH HYPOTHESIS ENGINE V1",
        "",
        "TOP HYPOTHESES (by Priority)",
    ]
    for h in g["top"][:10]:
        lines.append(f"  {_fmt_hyp_line(h)}")
    if not g["top"]:
        lines.append("  (empty — run hypothesis-validate)")

    for label, key in (
        ("NEW", STATUS_NEW),
        ("TESTING", STATUS_TESTING),
        ("VALIDATED", STATUS_VALIDATED),
        ("REJECTED", STATUS_REJECTED),
        ("LOW SAMPLE SIZE", "LOW_SAMPLE"),
    ):
        lines.append("")
        lines.append(label)
        rows = g[key][:10]
        for h in rows:
            lines.append(f"  {_fmt_hyp_line(h)}")
        if not rows:
            lines.append("  (none)")

    return "\n".join(lines)


def write_hypotheses_report(conn: Any, root: Path | None = None) -> Path:
    ensure_hypothesis_engine_schema(conn)
    bundle = load_hypothesis_bundle(conn)
    g = _group(bundle)
    out_dir = root or Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "hypotheses.md"

    lines = [
        "# Research Hypothesis Engine V1",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "_Research objects only. Not trade signals. No Gate / strategy / Trading Core changes._",
        "",
        "## Validated Hypotheses",
        "",
    ]
    if not g[STATUS_VALIDATED]:
        lines.append("_None_")
    for h in g[STATUS_VALIDATED]:
        lines.append(f"### {_fmt_hyp_line(h)}")
        lines.append("")
        lines.append(str(h.get("description") or ""))
        lines.append("")
        lines.append("Evidence:")
        for ev in h.get("evidence") or []:
            lines.append(
                f"- **{ev.get('source_type')}** / {ev.get('source_name')}: `{ev.get('reference')}` "
                f"(w={ev.get('weight')})"
            )
        lines.append("")

    lines.extend(["## Testing", ""])
    if not g[STATUS_TESTING]:
        lines.append("_None_")
    for h in g[STATUS_TESTING]:
        lines.append(f"- {_fmt_hyp_line(h)}")
        for ev in (h.get("evidence") or [])[:4]:
            lines.append(f"  - {ev.get('source_type')}/{ev.get('source_name')}: {ev.get('reference')}")

    lines.extend(["", "## Rejected", ""])
    if not g[STATUS_REJECTED]:
        lines.append("_None_")
    for h in g[STATUS_REJECTED][:20]:
        lines.append(f"- {_fmt_hyp_line(h)}")

    lines.extend(["", "## Low Sample", ""])
    if not g["LOW_SAMPLE"]:
        lines.append("_None_")
    for h in g["LOW_SAMPLE"][:20]:
        lines.append(f"- {_fmt_hyp_line(h)}")

    lines.extend(["", "## Top Opportunities", ""])
    if not g["top"]:
        lines.append("_None_")
    for h in g["top"][:10]:
        lines.append(f"### {_fmt_hyp_line(h)}")
        lines.append("")
        lines.append(str(h.get("description") or ""))
        lines.append("")
        lines.append("Evidence:")
        for ev in h.get("evidence") or []:
            lines.append(
                f"- **{ev.get('source_type')}** / {ev.get('source_name')}: `{ev.get('reference')}`"
            )
        lines.append("")

    lines.extend(["## Recent Changes", ""])
    try:
        hist = conn.execute(
            """
            SELECT * FROM knowledge_history
            WHERE entity_type = 'hypothesis'
            ORDER BY recorded_at DESC
            LIMIT 40
            """
        ).fetchall()
        hist = [dict(r) for r in hist]
    except Exception:
        hist = []
    if not hist:
        lines.append("_No hypothesis history yet_")
    for h in hist:
        ts = h.get("recorded_at")
        when = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if ts
            else "?"
        )
        lines.append(
            f"- {when} {h.get('entity_key')} {h.get('field_name')}: "
            f"{h.get('old_value')} → {h.get('new_value')}"
        )

    # NEW section for completeness
    lines.extend(["", "## NEW", ""])
    if not g[STATUS_NEW]:
        lines.append("_None_")
    for h in g[STATUS_NEW][:20]:
        lines.append(f"- {_fmt_hyp_line(h)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
