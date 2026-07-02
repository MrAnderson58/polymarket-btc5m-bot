"""READY / NOT READY checks for micro live trading."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bot.clob_client import verify_authentication, wallet_configured
from bot.config import (
    BASE_DIR,
    EARLY_REVERSION_POSITION_SIZE_USDC,
    LIVE_ENABLED,
    LIVE_MODE,
    TRADING_MODE,
    is_live_trading_enabled,
)
from bot.database import connect, init_db
from bot.optimizer.cache import is_cache_available, load_optimizer_cache
from bot.portfolio.guards import check_portfolio_guards
from bot.portfolio.kill_switch import is_kill_switch_active
from bot.portfolio.sizing import effective_position_size_usdc, max_allowed_usdc
from bot.scientist.builder import build_scientist_section
from bot.strategy_review.cache import load_strategy_review_cache
from bot.trading_brain.report import build_brain_report


@dataclass(frozen=True)
class CheckItem:
    name: str
    passed: bool
    details: str


def _cache_exists(path: Path) -> bool:
    return path.exists()


def run_live_checks(conn: sqlite3.Connection) -> list[CheckItem]:
    checks: list[CheckItem] = []

    api_ok = wallet_configured()
    if api_ok:
        try:
            verify_authentication()
            api_details = "Wallet configured and API auth OK"
            api_pass = True
        except Exception as exc:
            api_pass = False
            api_details = f"API auth failed: {exc}"
    else:
        api_pass = not is_live_trading_enabled()
        api_details = "Wallet not configured (OK for paper)"

    checks.append(CheckItem("API", api_pass, api_details))

    size = effective_position_size_usdc()
    size_ok = size <= max_allowed_usdc() <= 1.01
    checks.append(
        CheckItem(
            "Balance / sizing",
            size_ok,
            f"LIVE_MODE={LIVE_MODE} size=${size:.2f} (max ${max_allowed_usdc():.2f})",
        )
    )

    guard = check_portfolio_guards(conn)
    checks.append(
        CheckItem(
            "Risk / guards",
            guard.allowed and not is_kill_switch_active(),
            guard.reason or "Guards OK",
        )
    )

    db_ok = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'portfolio_state'"
    ).fetchone() is not None
    checks.append(CheckItem("Database", db_ok, "portfolio + live_journal tables"))

    opt_cache = is_cache_available(load_optimizer_cache())
    checks.append(
        CheckItem(
            "Optimizer cache",
            opt_cache,
            "optimizer_results.json present" if opt_cache else "run python -m bot.daily",
        )
    )

    intel_cache = _cache_exists(BASE_DIR / "intelligence_cache" / "intelligence_results.json")
    checks.append(
        CheckItem(
            "Intelligence cache",
            intel_cache,
            "intelligence_results.json present" if intel_cache else "run python -m bot.daily",
        )
    )

    brain = build_brain_report(conn)
    checks.append(
        CheckItem(
            "Brain",
            int(brain.get("contexts_stored", 0)) > 0,
            f"contexts={brain.get('contexts_stored', 0)}",
        )
    )

    scientist = build_scientist_section(conn, run_cycle=False)
    checks.append(
        CheckItem(
            "Scientist",
            bool(scientist.get("top_experiments") or scientist.get("patterns")),
            "scientist tables populated",
        )
    )

    review = load_strategy_review_cache()
    review_ok = bool(review.get("cached_at") or review.get("final_verdict"))
    checks.append(
        CheckItem(
            "Strategy Review",
            review_ok,
            review.get("final_verdict", {}).get("decision", "missing"),
        )
    )

    ai_ok = conn.execute("SELECT COUNT(*) AS n FROM ai_features").fetchone()["n"] > 0
    checks.append(CheckItem("AI", ai_ok, "ai_features rows present"))

    live_ok = LIVE_ENABLED and not is_kill_switch_active()
    checks.append(
        CheckItem(
            "Live enabled",
            live_ok or not is_live_trading_enabled(),
            f"LIVE_ENABLED={LIVE_ENABLED} TRADING_MODE={TRADING_MODE}",
        )
    )

    pos_ok = EARLY_REVERSION_POSITION_SIZE_USDC <= 1.01 or not is_live_trading_enabled()
    checks.append(
        CheckItem(
            "Position cap",
            pos_ok,
            f"EARLY_REVERSION_POSITION_SIZE_USDC={EARLY_REVERSION_POSITION_SIZE_USDC}",
        )
    )

    return checks


def readiness_verdict(checks: list[CheckItem]) -> tuple[bool, str]:
    failed = [c for c in checks if not c.passed]
    if failed:
        lines = ["NOT READY", ""]
        for item in failed:
            lines.append(f"- {item.name}: {item.details}")
        return False, "\n".join(lines)
    return True, "READY"


def main() -> int:
    init_db()
    with connect() as conn:
        checks = run_live_checks(conn)
        ready, message = readiness_verdict(checks)
        print(message)
        print("")
        for item in checks:
            mark = "OK" if item.passed else "FAIL"
            print(f"[{mark}] {item.name}: {item.details}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
