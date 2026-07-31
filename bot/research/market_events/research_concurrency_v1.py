"""Research Concurrency Fix V1 — stress harness for serialized research writers."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import is_database_locked
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema
from bot.research.market_events.knowledge_engine.store import _record_history
from bot.research.market_events.research_db_session import (
    research_write_connection,
)

STRESS_COMMANDS: tuple[str, ...] = (
    "feature-validation",
    "pattern-discovery",
    "hypothesis-validate",
    "experiment-run",
    "alpha-engine",
    "alpha-validate",
)

_FAST_ENV = {
    "ALPHA_ENGINE_MAX_RULES": "80",
    "ALPHA_ENGINE_N_BOOT": "20",
    "ALPHA_ENGINE_N_PERM": "15",
    "ALPHA_ENGINE_BACKFILL": "0",
    "ALPHA_VALIDATE_LIMIT": "5",
    "ALPHA_VALIDATE_N_MC": "20",
    "ALPHA_VALIDATE_N_BOOT": "20",
    "ALPHA_VALIDATE_N_PERM": "15",
    "ALPHA_VALIDATE_BACKFILL": "0",
}


def _python_cmd() -> list[str]:
    return [sys.executable, "-m", "bot.research.market_events"]


def run_cli_command(command: str, *, env: dict[str, str] | None = None, timeout: int = 600) -> dict[str, Any]:
    full_env = os.environ.copy()
    full_env.update(_FAST_ENV)
    if env:
        full_env.update(env)
    t0 = time.time()
    try:
        proc = subprocess.run(
            _python_cmd() + [command],
            cwd=str(BASE_DIR),
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        locked = "database is locked" in combined.lower() or "database table is locked" in combined.lower()
        return {
            "command": command,
            "returncode": int(proc.returncode),
            "locked": locked,
            "elapsed_sec": round(time.time() - t0, 3),
            "stderr_tail": (proc.stderr or "")[-500:],
        }
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or b"") if isinstance(exc.stdout, (bytes, bytearray)) else (exc.stdout or ""))
        err = ((exc.stderr or b"") if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or ""))
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", errors="replace")
        combined = f"{out}\n{err}"
        return {
            "command": command,
            "returncode": -1,
            "locked": "database is locked" in combined.lower(),
            "elapsed_sec": round(time.time() - t0, 3),
            "stderr_tail": "TIMEOUT",
            "error": "timeout",
        }
    except Exception as exc:
        return {
            "command": command,
            "returncode": -2,
            "locked": is_database_locked(exc),
            "elapsed_sec": round(time.time() - t0, 3),
            "stderr_tail": str(exc),
            "error": str(exc),
        }


def run_concurrent_cli_wave(
    *,
    commands: tuple[str, ...] | list[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    cmds = list(commands or STRESS_COMMANDS)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(cmds)) as pool:
        futs = {pool.submit(run_cli_command, c, env=env): c for c in cmds}
        for fut in as_completed(futs):
            results.append(fut.result())
    locked_n = sum(1 for r in results if r.get("locked"))
    return {
        "n_commands": len(cmds),
        "n_locked": locked_n,
        "results": results,
        "ok": locked_n == 0,
    }


def run_research_concurrency_stress(
    *,
    rounds: int = 20,
    commands: tuple[str, ...] | list[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the research CLI set concurrently for N rounds. Target: 0 database is locked."""
    waves = []
    total_locked = 0
    t0 = time.time()
    for i in range(int(rounds)):
        wave = run_concurrent_cli_wave(commands=commands, env=env)
        wave["round"] = i + 1
        waves.append(wave)
        total_locked += int(wave.get("n_locked") or 0)
        if total_locked:
            # Fail fast once we see locks (still report remaining? no — keep going for full evidence)
            pass
    return {
        "ok": total_locked == 0,
        "rounds": int(rounds),
        "total_locked": total_locked,
        "elapsed_sec": round(time.time() - t0, 3),
        "commands": list(commands or STRESS_COMMANDS),
        "waves": waves,
    }


def multithreaded_knowledge_history_stress(
    db_path: Path,
    *,
    n_threads: int = 12,
    n_writes_each: int = 40,
) -> dict[str, Any]:
    """Hammer knowledge_history via research_write_connection from many threads."""
    errors: list[str] = []
    locked = 0
    ok = 0
    counter_lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker(wid: int) -> None:
        nonlocal locked, ok
        try:
            barrier.wait(timeout=30)
        except Exception:
            pass
        try:
            with research_write_connection(db_path) as conn:
                ensure_knowledge_engine_schema(conn)
                apply_migrations(conn)
                for i in range(n_writes_each):
                    _record_history(
                        conn,
                        entity_type="stress",
                        entity_key=f"t{wid}",
                        field_name="counter",
                        old_value=str(i),
                        new_value=str(i + 1),
                        note=f"stress-{wid}-{i}",
                    )
                conn.commit()
            with counter_lock:
                ok += 1
        except Exception as exc:
            with counter_lock:
                if is_database_locked(exc):
                    locked += 1
                errors.append(f"t{wid}:{exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return {
        "ok": locked == 0 and not errors,
        "n_threads": n_threads,
        "n_writes_each": n_writes_each,
        "n_ok": ok,
        "n_locked": locked,
        "n_errors": len(errors),
        "errors": errors[:10],
    }


def format_stress_report(result: dict[str, Any]) -> str:
    lines = [
        "# RESEARCH_CONCURRENCY_STRESS",
        "",
        f"- ok: **{result.get('ok')}**",
        f"- rounds: **{result.get('rounds')}**",
        f"- total_locked: **{result.get('total_locked')}**",
        f"- elapsed_sec: **{result.get('elapsed_sec')}**",
        f"- commands: {', '.join(result.get('commands') or [])}",
        "",
    ]
    for wave in result.get("waves") or []:
        lines.append(
            f"- round {wave.get('round')}: locked={wave.get('n_locked')} "
            f"cmds={wave.get('n_commands')}"
        )
        for r in wave.get("results") or []:
            if r.get("locked") or r.get("returncode") not in (0, None):
                lines.append(
                    f"  - `{r.get('command')}` rc={r.get('returncode')} "
                    f"locked={r.get('locked')} err={r.get('stderr_tail', '')[:120]}"
                )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "STRESS_COMMANDS",
    "format_stress_report",
    "multithreaded_knowledge_history_stress",
    "run_cli_command",
    "run_concurrent_cli_wave",
    "run_research_concurrency_stress",
]
