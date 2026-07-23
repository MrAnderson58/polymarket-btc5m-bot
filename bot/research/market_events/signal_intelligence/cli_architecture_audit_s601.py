"""S60.1 — CLI & Architecture Audit.

Reconcile registered market_events CLI commands with documentation / Cursor
prompts, and report live vs research schema versions.
"""

from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Commands that only print a static Phase E doc (not a live system audit).
_DEPRECATED_WRAPPERS = frozenset({
    "e2-audit",  # dumps PHASE_E2 markdown only
})

# Explicit aliases (still implemented via multi-name dispatch).
_KNOWN_ALIASES: dict[str, str] = {
    "db-info": "market-db-info",
    "migrate-to-postgres": "market-db-copy",
    "explain": "explain-decision",
    "threshold-report": "near-miss-report / detector-stats family",
}

_CMD_TOKEN = re.compile(r"\b([a-z][a-z0-9]+(?:-[a-z0-9]+)+)\b")
_HELP_INVOCATION = re.compile(
    r"bot\.research\.market_events\s+([a-z][a-z0-9]+(?:-[a-z0-9]+)*)",
)
_DOC_GLOBS = (
    "README.md",
    "CHANGELOG.md",
    "docs/**/*.md",
    ".cursor/**/*.md",
    ".cursor/**/*.mdc",
    "deploy/**/*.md",
    "bot/**/README.md",
    "bot/research/ai_analyst/prompts/*.txt",
)


@dataclass
class CommandMention:
    command: str
    path: str
    line: int
    snippet: str


@dataclass
class CliArchitectureAudit:
    generated_at: int
    repo_root: str
    registered: list[str] = field(default_factory=list)
    dispatched: list[str] = field(default_factory=list)
    implemented: list[str] = field(default_factory=list)
    registered_without_dispatch: list[str] = field(default_factory=list)
    dispatch_without_registration: list[str] = field(default_factory=list)
    documented_only: list[str] = field(default_factory=list)
    undocumented_implemented: list[str] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    doc_mentions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    migrations: dict[str, Any] = field(default_factory=dict)
    recent_intelligence: dict[str, str] = field(default_factory=dict)
    ok: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _repo_root() -> Path:
    # bot/research/market_events/signal_intelligence/... → repo root
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return here.parents[4]


def _main_py(root: Path) -> Path:
    return root / "bot" / "research" / "market_events" / "__main__.py"


def extract_registered_commands(main_source: str) -> list[str]:
    """Parse argparse choices=(...) string literals from __main__.py."""
    tree = ast.parse(main_source)
    best: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "choices" or not isinstance(kw.value, (ast.Tuple, ast.List)):
                continue
            vals = [
                elt.value
                for elt in kw.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
            if len(vals) > len(best):
                best = vals
    return list(best)


def extract_dispatched_commands(main_source: str) -> set[str]:
    """Collect commands referenced in ``args.command ==`` / ``in (...)`` checks."""
    found: set[str] = set()
    for m in re.finditer(r'args\.command\s*==\s*["\']([^"\']+)["\']', main_source):
        found.add(m.group(1))
    for m in re.finditer(r"args\.command\s+in\s+\(([^)]*)\)", main_source, re.S):
        for s in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
            found.add(s)
    return found


def _iter_doc_files(root: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for pattern in _DOC_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            # Skip huge generated / vendored trees
            parts = set(path.parts)
            if "node_modules" in parts or ".git" in parts:
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            yield path


def scan_documented_commands(
    root: Path,
    *,
    known_commands: set[str],
) -> dict[str, list[CommandMention]]:
    """Find kebab-case command tokens in docs/prompts that look like CLI cmds."""
    mentions: dict[str, list[CommandMention]] = {}
    # Prefer explicit invocations; also catch backtick-wrapped command names
    # that match a known registered command OR look like market_events cmds.
    for path in _iter_doc_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(path.relative_to(root))
        for i, line in enumerate(text.splitlines(), start=1):
            hits: set[str] = set()
            for m in _HELP_INVOCATION.finditer(line):
                hits.add(m.group(1))
            # `command-name` or --help | grep command-name
            for m in re.finditer(r"`([a-z][a-z0-9]+(?:-[a-z0-9]+)+)`", line):
                hits.add(m.group(1))
            for m in re.finditer(r"grep\s+([a-z][a-z0-9]+(?:-[a-z0-9]+)+)", line):
                hits.add(m.group(1))
            for cmd in hits:
                # Keep if known OR looks like a staged research command
                if cmd not in known_commands and not _looks_like_cli_command(cmd):
                    continue
                mentions.setdefault(cmd, []).append(
                    CommandMention(
                        command=cmd,
                        path=rel,
                        line=i,
                        snippet=line.strip()[:160],
                    ),
                )
    return mentions


def _looks_like_cli_command(token: str) -> bool:
    """Heuristic for undocumented planned commands in prose."""
    if token.count("-") < 1:
        return False
    prefixes = (
        "market-", "trade-", "signal-", "paper-", "research-", "strategy-",
        "decision-", "feature-", "learning-", "shadow-", "validation-",
        "telegram-", "shock-", "historical-", "ai-", "cli-", "sqlite-",
    )
    return token.startswith(prefixes) or token.endswith(
        ("-report", "-audit", "-migrate", "-test", "-status", "-worker"),
    )


def collect_migration_info() -> dict[str, Any]:
    from bot.research.market_events.event_schema import (
        LIVE_SCHEMA_VERSION,
        MIGRATIONS_TABLE,
        SCHEMA_VERSION,
    )
    from bot.research.market_events.signal_intelligence.research_repository_s60 import (
        RESEARCH_MIGRATIONS_TABLE,
        RESEARCH_SCHEMA_VERSION,
        resolve_research_db_config,
    )
    from bot.research.market_events.db_config import resolve_market_events_db_config

    live_cfg = resolve_market_events_db_config()
    research_cfg = resolve_research_db_config()

    info: dict[str, Any] = {
        "code": {
            "LIVE_SCHEMA_VERSION": LIVE_SCHEMA_VERSION,
            "SCHEMA_VERSION": SCHEMA_VERSION,
            "RESEARCH_SCHEMA_VERSION": RESEARCH_SCHEMA_VERSION,
            "live_migrations_table": MIGRATIONS_TABLE,
            "research_migrations_table": RESEARCH_MIGRATIONS_TABLE,
        },
        "live_db": {
            "backend": live_cfg.backend,
            "url_source": live_cfg.config_source,
            "applied_version": None,
            "error": None,
        },
        "research_db": {
            "backend": research_cfg.backend,
            "url_source": research_cfg.config_source,
            "separated": research_cfg.separated,
            "applied_version": None,
            "error": None,
        },
        "research_steps": list(range(66, RESEARCH_SCHEMA_VERSION + 1)),
    }

    try:
        from bot.research.market_events.db import market_events_connection

        with market_events_connection() as conn:
            row = conn.execute(
                f"SELECT MAX(version) AS v FROM {MIGRATIONS_TABLE}",
            ).fetchone()
            info["live_db"]["applied_version"] = int(row["v"] or 0) if row else 0
    except Exception as exc:
        info["live_db"]["error"] = str(exc)[:160]

    try:
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )

        with research_connection(readonly=True) as conn:
            row = conn.execute(
                f"SELECT MAX(version) AS v FROM {RESEARCH_MIGRATIONS_TABLE}",
            ).fetchone()
            info["research_db"]["applied_version"] = int(row["v"] or 0) if row else 0
    except Exception as exc:
        info["research_db"]["error"] = str(exc)[:160]

    return info


def _recent_intelligence_status(implemented: set[str]) -> dict[str, str]:
    """Human checklist for S56–S61 stage commands."""
    stages = {
        "trade-postmortem": "S56",
        "market-regime": "S57",
        "decision-report": "S58",
        "explain-decision": "S58",
        "feature-lab": "S59",
        "market-research-migrate": "S60",
        "research-stress-test": "S60",
        "strategy-discovery": "S61",
        "cli-architecture-audit": "S60.1",
    }
    out: dict[str, str] = {}
    for cmd, stage in stages.items():
        mark = "implemented" if cmd in implemented else "missing"
        out[f"{stage} {cmd}"] = mark
    return out


def run_cli_architecture_audit(*, root: Path | None = None) -> CliArchitectureAudit:
    root = root or _repo_root()
    main_path = _main_py(root)
    source = main_path.read_text(encoding="utf-8")

    registered = sorted(set(extract_registered_commands(source)))
    dispatched = extract_dispatched_commands(source)
    reg_set = set(registered)
    disp_set = set(dispatched)
    implemented = sorted(reg_set & disp_set)
    impl_set = set(implemented)

    documented = scan_documented_commands(root, known_commands=reg_set | disp_set)
    doc_cmds = set(documented.keys())

    documented_only = sorted(
        c for c in doc_cmds
        if c not in impl_set and c not in reg_set
    )
    undocumented = sorted(
        c for c in implemented
        if c not in doc_cmds and c not in _DEPRECATED_WRAPPERS
    )

    deprecated = sorted(
        c for c in (_DEPRECATED_WRAPPERS & (reg_set | disp_set))
    )

    migrations = collect_migration_info()
    notes: list[str] = []
    if registered and not (reg_set - disp_set):
        notes.append("All argparse choices have a dispatch handler.")
    missing_disp = sorted(reg_set - disp_set)
    if missing_disp:
        notes.append(f"{len(missing_disp)} registered commands lack dispatch.")
    code_live = migrations["code"]["LIVE_SCHEMA_VERSION"]
    code_research = migrations["code"]["RESEARCH_SCHEMA_VERSION"]
    notes.append(
        f"Live schema target={code_live}; research schema target={code_research}; "
        f"project watermark SCHEMA_VERSION={migrations['code']['SCHEMA_VERSION']}.",
    )

    ok = not bool(missing_disp)

    return CliArchitectureAudit(
        generated_at=int(time.time()),
        repo_root=str(root),
        registered=sorted(registered),
        dispatched=sorted(dispatched),
        implemented=implemented,
        registered_without_dispatch=missing_disp,
        dispatch_without_registration=sorted(disp_set - reg_set),
        documented_only=documented_only,
        undocumented_implemented=undocumented,
        deprecated=deprecated,
        aliases=dict(_KNOWN_ALIASES),
        doc_mentions={
            k: [asdict(m) for m in v[:5]]
            for k, v in sorted(documented.items())
        },
        migrations=migrations,
        recent_intelligence=_recent_intelligence_status(impl_set),
        ok=ok and not missing_disp,
        notes=notes,
    )


def format_cli_architecture_audit(audit: CliArchitectureAudit) -> str:
    lines: list[str] = [
        "S60.1 CLI & Architecture Audit",
        f"  generated_at={audit.generated_at}  ok={audit.ok}",
        f"  registered={len(audit.registered)}  dispatched={len(audit.dispatched)}  "
        f"implemented={len(audit.implemented)}",
        "",
        "Implemented (registered + dispatch):",
    ]
    # Highlight recent intelligence first, then count summary
    for key, status in audit.recent_intelligence.items():
        mark = "✓" if status == "implemented" else "✗"
        lines.append(f"  {mark} {key}")

    lines.extend([
        "",
        f"  … plus {max(0, len(audit.implemented) - sum(1 for s in audit.recent_intelligence.values() if s == 'implemented'))} "
        f"other implemented commands (see --json for full list)",
        "",
        "Documented only (mentioned in docs/prompts, not implemented):",
    ])
    if not audit.documented_only:
        lines.append("  (none)")
    else:
        for cmd in audit.documented_only:
            hits = audit.doc_mentions.get(cmd) or []
            where = hits[0]["path"] if hits else "?"
            lines.append(f"  ✗ {cmd}  (e.g. {where})")

    lines.extend(["", "Undocumented implemented (in code, no docs/prompt hit):"])
    if not audit.undocumented_implemented:
        lines.append("  (none)")
    else:
        # Cap display
        for cmd in audit.undocumented_implemented[:40]:
            lines.append(f"  · {cmd}")
        extra = len(audit.undocumented_implemented) - 40
        if extra > 0:
            lines.append(f"  … +{extra} more")

    lines.extend(["", "Deprecated / static-doc wrappers:"])
    if not audit.deprecated:
        lines.append("  (none)")
    else:
        for cmd in audit.deprecated:
            lines.append(f"  ! {cmd}")

    lines.extend(["", "Registration gaps:"])
    if audit.registered_without_dispatch:
        for cmd in audit.registered_without_dispatch:
            lines.append(f"  ✗ registered without dispatch: {cmd}")
    else:
        lines.append("  ✓ every choices= entry has a dispatch handler")
    if audit.dispatch_without_registration:
        for cmd in audit.dispatch_without_registration:
            lines.append(f"  ! dispatch without choices=: {cmd}")

    mig = audit.migrations
    code = mig.get("code") or {}
    live = mig.get("live_db") or {}
    research = mig.get("research_db") or {}
    lines.extend([
        "",
        "Migrations:",
        f"  LIVE_SCHEMA_VERSION (code)     = {code.get('LIVE_SCHEMA_VERSION')}",
        f"  RESEARCH_SCHEMA_VERSION (code) = {code.get('RESEARCH_SCHEMA_VERSION')}",
        f"  SCHEMA_VERSION watermark       = {code.get('SCHEMA_VERSION')}",
        f"  live applied                   = {live.get('applied_version')} "
        f"({live.get('backend')}/{live.get('url_source')})"
        + (f" err={live.get('error')}" if live.get("error") else ""),
        f"  research applied               = {research.get('applied_version')} "
        f"separated={research.get('separated')} ({research.get('backend')}/{research.get('url_source')})"
        + (f" err={research.get('error')}" if research.get("error") else ""),
        f"  research steps                 = {mig.get('research_steps')}",
        "",
        "Aliases:",
    ])
    for a, target in sorted(audit.aliases.items()):
        lines.append(f"  {a} → {target}")

    if audit.notes:
        lines.append("")
        lines.append("Notes:")
        for n in audit.notes:
            lines.append(f"  - {n}")

    return "\n".join(lines)


def write_audit_markdown(audit: CliArchitectureAudit, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# S60.1 CLI & Architecture Audit",
        "",
        f"Generated: `{audit.generated_at}`",
        "",
        "```",
        format_cli_architecture_audit(audit),
        "```",
        "",
        "## Full implemented list",
        "",
    ]
    for cmd in audit.implemented:
        body.append(f"- `{cmd}`")
    body.append("")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


__all__ = [
    "CliArchitectureAudit",
    "format_cli_architecture_audit",
    "run_cli_architecture_audit",
    "write_audit_markdown",
]
