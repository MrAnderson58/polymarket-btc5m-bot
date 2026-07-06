"""Architecture and infrastructure audit for futures agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.research.futures.db_config import get_futures_source_database_url
from bot.research.futures_agent.config import get_agent_database_url, telegram_notify_enabled
from bot.research.futures_agent.env_bootstrap import (
    bootstrap_config,
    db_config_diagnostics,
    project_root,
    resolve_agent_db_config,
)
from bot.research.futures_agent.db import resolve_agent_url
from bot.research.futures_agent.schema_validate import validate_stage1_schema


def run_architecture_audit() -> dict[str, Any]:
    bootstrap_config()
    cfg = resolve_agent_db_config()
    repo = project_root()
    report_path = repo / "reports" / "futures_agent_architecture_audit.md"

    futures_pkg = repo / "bot" / "research" / "futures"
    agent_pkg = repo / "bot" / "research" / "futures_agent"

    modules = {
        "parser_v2": (futures_pkg / "parser_v2.py").exists(),
        "taxonomy": (futures_pkg / "taxonomy.py").exists(),
        "source_reader": (futures_pkg / "source_reader.py").exists(),
        "agent_db": (agent_pkg / "db.py").exists(),
        "agent_ingestion": (agent_pkg / "ingestion.py").exists(),
        "architecture_report": report_path.exists(),
    }

    schema_validation: dict[str, Any] | None = None
    try:
        from bot.research.futures_agent.db import agent_connection
        with agent_connection() as conn:
            schema_validation = validate_stage1_schema(conn)
    except Exception as exc:
        schema_validation = {"valid": False, "error": str(exc)}

    return {
        "repository": str(repo),
        "architecture_report": str(report_path),
        "agent_database_url_configured": bool(get_agent_database_url()),
        "agent_database_resolved": resolve_agent_url(),
        "db_diagnostics": db_config_diagnostics(cfg),
        "source_database_url_configured": bool(get_futures_source_database_url()),
        "schema_validation": schema_validation,
        "telegram_notify_configured": telegram_notify_enabled(),
        "telegram_inbound_bot": False,
        "telegram_inbound_note": (
            "No inbound bot in btc5m-bot; optional poller in futures_agent (Stage 1b). "
            "polymarket-ai has outbound-only TelegramNotifier."
        ),
        "reusable_modules": modules,
        "execution_isolation": _check_no_execution_imports(agent_pkg),
        "integration_point": "bot/research/futures_agent/",
        "stage1_tables": [
            "futures_agent_inputs",
            "futures_agent_signals",
            "futures_agent_targets",
            "futures_agent_migrations",
        ],
    }


def _check_no_execution_imports(agent_pkg: Path) -> dict[str, bool]:
    forbidden_imports = ("from bot.execution", "import bot.execution", "from bot.main", "import bot.main")
    violations: list[str] = []
    for py in agent_pkg.glob("*.py"):
        if py.name == "audit.py":
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for f in forbidden_imports:
                if f in stripped:
                    violations.append(f"{py.name}: {stripped}")
    return {"clean": len(violations) == 0, "violations": violations}


def render_audit(report: dict[str, Any]) -> str:
    lines = [
        "FUTURES AGENT ARCHITECTURE AUDIT",
        "=" * 50,
        f"Repository: {report['repository']}",
        f"Report doc: {report['architecture_report']}",
        "",
        "DATABASE BACKEND (active)",
    ]
    diag = report.get("db_diagnostics", {})
    lines.append(f"  Backend: {diag.get('backend')}")
    lines.append(f"  Config source: {diag.get('config_source')}")
    lines.append(f"  Database name: {diag.get('database_name')}")
    lines.append(f"  SQLite path: {diag.get('sqlite_path')}")
    lines.append(f"  Postgres URL configured: {diag.get('postgres_url_configured')}")
    lines.append(f"  Resolved URL scheme: {diag.get('url_scheme')}")
    lines.extend([
        "",
        "LEGACY FIELDS",
        f"  Agent URL in env: {report['agent_database_url_configured']}",
        f"  Resolved URL: {report['agent_database_resolved']}",
        f"  Source (read-only) URL: {report['source_database_url_configured']}",
    ])
    sv = report.get("schema_validation")
    if sv:
        lines.extend(["", "SCHEMA VALIDATION"])
        if sv.get("valid"):
            lines.append(f"  Valid: yes ({sv.get('tables_ok')})")
        else:
            lines.append(f"  Valid: no — {sv.get('errors') or sv.get('error')}")
    lines.extend([
        "",
        "TELEGRAM",
        f"  Outbound notify configured: {report['telegram_notify_configured']}",
        f"  Inbound bot present: {report['telegram_inbound_bot']}",
        f"  Note: {report['telegram_inbound_note']}",
        "",
        "REUSABLE MODULES",
    ])
    for k, v in report["reusable_modules"].items():
        lines.append(f"  {k}: {'yes' if v else 'no'}")
    lines.extend([
        "",
        "EXECUTION ISOLATION",
        f"  Clean: {report['execution_isolation']['clean']}",
    ])
    if report["execution_isolation"]["violations"]:
        for v in report["execution_isolation"]["violations"]:
            lines.append(f"    VIOLATION: {v}")
    lines.extend(["", "STAGE 1 TABLES"])
    for t in report["stage1_tables"]:
        lines.append(f"  - {t}")
    return "\n".join(lines)
