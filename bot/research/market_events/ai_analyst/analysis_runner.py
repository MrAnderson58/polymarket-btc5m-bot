"""Run a single AI analysis job and persist results."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.ai_analyst.config import PROMPT_VERSION
from bot.research.market_events.ai_analyst.context_bundle import build_context_bundle
from bot.research.market_events.ai_analyst.job_queue import JOB_COMPLETE, JOB_FAILED, JOB_RUNNING
from bot.research.market_events.ai_analyst.provider import get_analyst_provider
from bot.research.market_events.db import insert_returning_id


def run_analysis_job(conn: Any, job_id: int) -> bool:
    row = conn.execute(
        "SELECT * FROM market_event_analysis_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if not row or row["status"] not in ("pending", "failed"):
        return False

    now = int(time.time())
    conn.execute(
        """
        UPDATE market_event_analysis_jobs SET
          status = ?, started_at = ?, attempts = attempts + 1, updated_at = ?
        WHERE id = ?
        """,
        (JOB_RUNNING, now, now, job_id),
    )

    event_id = int(row["event_id"])
    bundle = build_context_bundle(conn, event_id)
    provider = get_analyst_provider()
    result = provider.analyze_market_event(bundle)

    analysis = result.analysis
    if analysis is None:
        conn.execute(
            """
            UPDATE market_event_analysis_jobs SET
              status = ?, completed_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (JOB_FAILED, int(time.time()), result.error or "no_analysis", int(time.time()), job_id),
        )
        return False

    status = JOB_COMPLETE if analysis.analysis_status != "FAILED" else JOB_FAILED
    insert_returning_id(
        conn,
        """
        INSERT INTO market_event_ai_analyses (
          event_id, job_id, provider, model, prompt_version,
          structured_output_json, movement_interpretation, reversal_bias,
          confidence, context_ids_json, latency_ms, token_usage_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, job_id, result.provider, result.model, result.prompt_version,
            json.dumps(analysis.to_dict()),
            analysis.movement_interpretation, analysis.reversal_bias,
            analysis.confidence,
            json.dumps(analysis.relevant_context_ids),
            result.latency_ms,
            json.dumps(result.token_usage),
            int(time.time()),
        ),
    )
    conn.execute(
        """
        UPDATE market_event_analysis_jobs SET
          status = ?, completed_at = ?, last_error = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, int(time.time()), result.error, int(time.time()), job_id),
    )

    if analysis.analysis_status == "COMPLETE":
        _maybe_send_ai_commentary(conn, event_id, analysis)
    return True


def _maybe_send_ai_commentary(conn: Any, event_id: int, analysis: Any) -> None:
    try:
        from bot.research.market_events.market_event_alerts import alert_ai_research_note

        evt = conn.execute(
            "SELECT symbol, return_pct FROM market_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        ret = evt["return_pct"] if evt else 0
        sym = evt["symbol"] if evt else analysis.symbol
        lines = [
            f"{sym} {ret:+.2f}%",
            f"Interpretation: {analysis.movement_interpretation}",
            "",
            "Supporting:",
        ]
        lines.extend(f"- {s}" for s in analysis.supporting_factors[:5])
        if analysis.contradicting_factors:
            lines.append("")
            lines.append("Against immediate fade:")
            lines.extend(f"- {c}" for c in analysis.contradicting_factors[:5])
        lines.extend([
            "",
            f"Bias: {analysis.reversal_bias}",
            f"Confidence: {analysis.confidence:.2f}",
        ])
        alert_ai_research_note(conn, event_id, "\n".join(lines))
    except Exception:
        pass
