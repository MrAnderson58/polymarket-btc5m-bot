"""Knowledge Engine reports and knowledge-show CLI."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema


def _rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def load_knowledge_snapshot(conn: Any) -> dict[str, Any]:
    ensure_knowledge_engine_schema(conn)
    features = _rows(
        conn,
        "SELECT * FROM knowledge_features ORDER BY confidence DESC, sample_size DESC",
    )
    rules = _rows(
        conn,
        "SELECT * FROM knowledge_rules ORDER BY confidence DESC, sample_size DESC",
    )
    interactions = _rows(
        conn,
        """
        SELECT * FROM knowledge_interactions
        ORDER BY ABS(COALESCE(synergy, 0)) DESC, sample_size DESC
        """,
    )
    history = _rows(
        conn,
        """
        SELECT * FROM knowledge_history
        ORDER BY recorded_at DESC
        LIMIT 50
        """,
    )
    return {
        "features": features,
        "rules": rules,
        "interactions": interactions,
        "history": history,
        "validated": [r for r in rules if r.get("status") == "validated"],
        "candidates": [r for r in rules if r.get("status") == "candidate"],
        "rejected": [r for r in rules if r.get("status") == "rejected"],
    }


def write_knowledge_report(conn: Any, root: Path | None = None) -> Path:
    data = load_knowledge_snapshot(conn)
    out_dir = root or Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "knowledge.md"
    lines = [
        "# Knowledge Engine V1",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "_Machine-validated analytics memory. Not trade signals. LLM should read this layer, not raw SQLite._",
        "",
        "## Validated Features / Rules",
        "",
    ]
    if not data["validated"]:
        lines.append("_None yet_")
    for r in data["validated"]:
        lines.append(
            f"- **{r['feature_name']}** — `{r['rule_text']}`  "
            f"EV Δ={r.get('ev_delta')}  conf={r.get('confidence')}%  n={r.get('sample_size')}  "
            f"status={r.get('status')}"
        )
    lines.extend(["", "## New Candidates", ""])
    if not data["candidates"]:
        lines.append("_None_")
    for r in data["candidates"]:
        lines.append(
            f"- **{r['feature_name']}** — `{r['rule_text']}`  "
            f"EV Δ={r.get('ev_delta')}  conf={r.get('confidence')}%  n={r.get('sample_size')}"
        )
    lines.extend(["", "## Rejected Features / Rules", ""])
    if not data["rejected"]:
        lines.append("_None_")
    for r in data["rejected"]:
        lines.append(
            f"- **{r['feature_name']}** — `{r['rule_text']}`  "
            f"EV Δ={r.get('ev_delta')}  n={r.get('sample_size')}"
        )
    lines.extend(["", "## Interactions", ""])
    if not data["interactions"]:
        lines.append("_None_")
    for ix in data["interactions"][:25]:
        flag = " stronger_together" if ix.get("stronger_together") else ""
        lines.append(
            f"- {ix['feature_a']} + {ix['feature_b']}: synergy={ix.get('synergy')}  "
            f"EV_joint={ix.get('ev_joint')}  n={ix.get('sample_size')}{flag}"
        )
    lines.extend(["", "## Recent Changes", ""])
    if not data["history"]:
        lines.append("_No history yet_")
    for h in data["history"][:30]:
        ts = h.get("recorded_at")
        when = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "?"
        lines.append(
            f"- {when} [{h.get('entity_type')}] {h.get('entity_key')} "
            f"{h.get('field_name')}: {h.get('old_value')} → {h.get('new_value')}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def format_knowledge_show(conn: Any) -> str:
    data = load_knowledge_snapshot(conn)
    lines = [
        "KNOWLEDGE ENGINE V1",
        "",
        "Top Features",
    ]
    for f in (data["features"] or [])[:10]:
        lines.append(
            f"  {f.get('feature_name')}: verdict={f.get('verdict')} "
            f"ΔEV={f.get('ev_delta')} conf={f.get('confidence')} n={f.get('sample_size')}"
        )
    if not data["features"]:
        lines.append("  (empty — run feature-validation first)")
    lines.append("")
    lines.append("Top Rules")
    for r in (data["rules"] or [])[:10]:
        lines.append(
            f"  [{r.get('status')}] {r.get('feature_name')}: {r.get('rule_text')} "
            f"ΔEV={r.get('ev_delta')} conf={r.get('confidence')}%"
        )
    if not data["rules"]:
        lines.append("  (empty)")
    lines.append("")
    lines.append("Top Interactions")
    for ix in (data["interactions"] or [])[:10]:
        lines.append(
            f"  {ix.get('feature_a')}+{ix.get('feature_b')}: synergy={ix.get('synergy')} n={ix.get('sample_size')}"
        )
    if not data["interactions"]:
        lines.append("  (empty)")
    lines.append("")
    lines.append("Recent Changes")
    for h in (data["history"] or [])[:10]:
        lines.append(
            f"  {h.get('entity_type')}/{h.get('entity_key')} {h.get('field_name')}: "
            f"{h.get('old_value')} → {h.get('new_value')}"
        )
    if not data["history"]:
        lines.append("  (empty)")
    return "\n".join(lines)
