"""Evolution History — persistent timeline of all strategy experiments.

Tracks every shadow experiment (parameter or regime filter) and its outcome,
building a version history of how the strategy evolved.

Example timeline:
  Version 1 | Entry 0.40 | Shadow | Rejected
  Version 2 | Entry 0.39 | Shadow | Promoted
  Version 3 | BTC Filter | Shadow | Running
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bot.config import BASE_DIR

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _next_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM evolution_history").fetchone()
    return int(row["v"] or 0) + 1


def record_experiment_start(
    conn: sqlite3.Connection,
    *,
    experiment_type: str,
    parameter: str,
    from_value: str | None = None,
    to_value: str | None = None,
    description: str,
    shadow_id: int | None = None,
) -> int:
    """Record a new experiment in the history. Returns version number."""
    existing = conn.execute(
        """
        SELECT id FROM evolution_history
        WHERE shadow_id = ? AND experiment_type = ? AND status = 'RUNNING'
        """,
        (shadow_id, experiment_type),
    ).fetchone()
    if existing:
        return int(conn.execute(
            "SELECT version FROM evolution_history WHERE id = ?", (existing["id"],)
        ).fetchone()["version"])

    version = _next_version(conn)
    conn.execute(
        """
        INSERT INTO evolution_history (
            version, experiment_type, parameter, from_value, to_value,
            description, status, shadow_id, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)
        """,
        (version, experiment_type, parameter, from_value, to_value, description, shadow_id, _utc_now()),
    )
    logger.info("EVOLUTION_HISTORY | v%d | START | %s | %s", version, experiment_type, description)
    return version


def record_experiment_complete(
    conn: sqlite3.Connection,
    *,
    shadow_id: int,
    experiment_type: str,
    verdict: str,
    metrics: dict[str, Any] | None = None,
) -> None:
    """Mark an experiment as promoted or rejected."""
    status = "PROMOTED" if "PROMOTE" in verdict.upper() else "REJECTED"
    conn.execute(
        """
        UPDATE evolution_history
        SET status = ?,
            completed_at = ?,
            metrics_json = ?
        WHERE shadow_id = ? AND experiment_type = ? AND status = 'RUNNING'
        """,
        (status, _utc_now(), json.dumps(metrics) if metrics else None, shadow_id, experiment_type),
    )
    logger.info("EVOLUTION_HISTORY | %s | shadow_id=%s | %s", status, shadow_id, verdict)


def load_history(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Load full evolution history ordered by version."""
    rows = conn.execute(
        "SELECT * FROM evolution_history ORDER BY version ASC"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Auto-sync: ensure current experiments are tracked in history
# ---------------------------------------------------------------------------

def sync_history_from_shadows(conn: sqlite3.Connection) -> None:
    """Ensure all shadow experiments have a history entry."""
    shadows = conn.execute(
        "SELECT * FROM evolution_shadow ORDER BY id ASC"
    ).fetchall()
    for s in shadows:
        existing = conn.execute(
            "SELECT id FROM evolution_history WHERE shadow_id = ? AND experiment_type = 'parameter'",
            (s["id"],),
        ).fetchone()
        if existing:
            if s["status"] == "COMPLETE" and s.get("verdict"):
                record_experiment_complete(
                    conn,
                    shadow_id=s["id"],
                    experiment_type="parameter",
                    verdict=s["verdict"],
                    metrics={
                        "live_pf": s.get("live_pf"),
                        "shadow_pf": s.get("shadow_pf"),
                        "live_wr": s.get("live_wr"),
                        "shadow_wr": s.get("shadow_wr"),
                    },
                )
            continue
        from_val = str(s["current_value"])
        to_val = str(s["shadow_value"])
        record_experiment_start(
            conn,
            experiment_type="parameter",
            parameter=s["parameter"],
            from_value=from_val,
            to_value=to_val,
            description=f"{s['parameter']} {from_val} → {to_val}",
            shadow_id=s["id"],
        )
        if s["status"] == "COMPLETE" and s.get("verdict"):
            record_experiment_complete(
                conn,
                shadow_id=s["id"],
                experiment_type="parameter",
                verdict=s["verdict"],
                metrics={
                    "live_pf": s.get("live_pf"),
                    "shadow_pf": s.get("shadow_pf"),
                },
            )

    regime_shadows = conn.execute(
        "SELECT * FROM evolution_regime_shadow ORDER BY id ASC"
    ).fetchall()
    for rs in regime_shadows:
        existing = conn.execute(
            "SELECT id FROM evolution_history WHERE shadow_id = ? AND experiment_type = 'regime_filter'",
            (rs["id"],),
        ).fetchone()
        if existing:
            if rs["status"] == "COMPLETE" and rs.get("verdict"):
                record_experiment_complete(
                    conn,
                    shadow_id=rs["id"],
                    experiment_type="regime_filter",
                    verdict=rs["verdict"],
                    metrics={"net_pf_improvement_pct": rs.get("net_pf_improvement_pct")},
                )
            continue
        regimes = json.loads(rs["regimes_json"])
        record_experiment_start(
            conn,
            experiment_type="regime_filter",
            parameter=rs["filter_name"],
            description=f"Filter: {', '.join(regimes)}",
            shadow_id=rs["id"],
        )
        if rs["status"] == "COMPLETE" and rs.get("verdict"):
            record_experiment_complete(
                conn,
                shadow_id=rs["id"],
                experiment_type="regime_filter",
                verdict=rs["verdict"],
                metrics={"net_pf_improvement_pct": rs.get("net_pf_improvement_pct")},
            )


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "RUNNING": "→ Shadow",
    "PROMOTED": "✓ Promoted",
    "REJECTED": "✗ Rejected",
}


def render_history_block(history: list[dict[str, Any]]) -> str:
    if not history:
        return ""

    lines = [
        "----------------------------------",
        "",
        "EVOLUTION HISTORY",
        "",
    ]
    for entry in history:
        version = entry["version"]
        param = entry["parameter"]
        status = _STATUS_ICONS.get(entry["status"], entry["status"])
        desc = ""
        if entry.get("from_value") and entry.get("to_value"):
            desc = f"{entry['from_value']} → {entry['to_value']}"
        elif entry.get("description"):
            desc = entry["description"][:40]

        lines.append(f"  Version {version}  {param} {desc}")
        lines.append(f"             {status}")
        lines.append("")

    lines.extend(["----------------------------------", ""])
    return "\n".join(lines)


def render_history_section(history: list[dict[str, Any]]) -> list[str]:
    if not history:
        return ["", "_No evolution history yet._", ""]

    lines = [
        "",
        "| Version | Type | Parameter | Change | Status | Date |",
        "|---------|------|-----------|--------|--------|------|",
    ]
    for entry in history:
        change = ""
        if entry.get("from_value") and entry.get("to_value"):
            change = f"{entry['from_value']} → {entry['to_value']}"
        status_icon = _STATUS_ICONS.get(entry["status"], entry["status"])
        date = (entry.get("completed_at") or entry.get("started_at") or "")[:10]
        lines.append(
            f"| {entry['version']} | {entry['experiment_type']} | "
            f"{entry['parameter']} | {change} | {status_icon} | {date} |"
        )

    lines.append("")
    lines.append("_Complete record of strategy evolution decisions._")
    return lines


def save_history_file(conn: sqlite3.Connection) -> None:
    """Save evolution history to reports/evolution_history.md."""
    history = load_history(conn)
    if not history:
        return

    reports_dir = BASE_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    lines = ["# Strategy Evolution History", ""]
    for entry in history:
        version = entry["version"]
        param = entry["parameter"]
        status = _STATUS_ICONS.get(entry["status"], entry["status"])
        lines.append(f"## Version {version}")
        lines.append(f"**Type:** {entry['experiment_type']}")
        lines.append(f"**Parameter:** {param}")
        if entry.get("from_value") and entry.get("to_value"):
            lines.append(f"**Change:** {entry['from_value']} → {entry['to_value']}")
        if entry.get("description"):
            lines.append(f"**Description:** {entry['description']}")
        lines.append(f"**Status:** {status}")
        lines.append(f"**Started:** {entry.get('started_at', '?')}")
        if entry.get("completed_at"):
            lines.append(f"**Completed:** {entry['completed_at']}")
        if entry.get("metrics_json"):
            metrics = json.loads(entry["metrics_json"])
            for k, v in metrics.items():
                if v is not None:
                    lines.append(f"**{k}:** {v}")
        lines.append("")

    path = reports_dir / "evolution_history.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("EVOLUTION_HISTORY | saved to %s", path)
