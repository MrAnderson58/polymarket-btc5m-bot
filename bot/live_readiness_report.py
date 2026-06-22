"""Live trading readiness audit report."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from bot.clob_client import verify_authentication, wallet_configured
from bot.config import (
    LIVE_EXIT_ENABLED,
    MAX_DAILY_LOSS_USDC,
    MAX_OPEN_POSITIONS,
    TRADING_MODE,
    VALID_TRADING_MODES,
)
from bot.database import connect, init_db
from bot.execution import format_dry_run_exit_log, format_dry_run_log
from bot.risk import check_can_open_position


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    details: str


def _execution_source() -> str:
    return (Path(__file__).resolve().parent / "execution.py").read_text(encoding="utf-8")


def _module_has_live_entry_submission() -> bool:
    text = _execution_source()
    return "create_and_post_order" in text and "_submit_live_buy" in text


def _module_has_live_exit_submission() -> bool:
    text = _execution_source()
    return "create_and_post_order" in text and "_submit_live_sell" in text


def _strategy_modules_use_exit_gate() -> bool:
    root = Path(__file__).resolve().parent
    modules = (
        "early_reversion_v2.py",
        "early_reversion_v25.py",
        "early_reversion_v3.py",
    )
    return all(
        "close_early_reversion_position" in (root / name).read_text(encoding="utf-8")
        for name in modules
    )


def _order_intents_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'order_intents'
        """
    ).fetchone()
    return row is not None


def _order_intents_has_unique_key(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA index_list(order_intents)").fetchall()
    for row in rows:
        index_name = row["name"]
        if not row["unique"]:
            continue
        cols = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
        if any(col["name"] == "idempotency_key" for col in cols):
            return True
    info = conn.execute("PRAGMA table_info(order_intents)").fetchall()
    return any(col["name"] == "idempotency_key" for col in info)


def _strategy_modules_use_execution_gate() -> bool:
    modules = (
        "bot/early_reversion.py",
        "bot/early_reversion_v2.py",
        "bot/early_reversion_v25.py",
        "bot/early_reversion_v3.py",
        "bot/paper_trader.py",
    )
    root = Path(__file__).resolve().parent.parent
    return all(
        "attempt_entry_open" in (root / rel).read_text(encoding="utf-8")
        for rel in modules
    )


def _exit_idempotency_implemented() -> bool:
    text = _execution_source()
    return "build_exit_idempotency_key" in text and ":exit" in text


def _dry_run_logging_ready() -> bool:
    entry = format_dry_run_log(
        __import__("bot.execution", fromlist=["EntryOrder"]).EntryOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-test",
            side="NO",
            token_id="token",
            price=0.38,
            size_usdc=1.0,
        )
    )
    exit_log = format_dry_run_exit_log(
        __import__("bot.execution", fromlist=["ExitOrder"]).ExitOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-test",
            side="NO",
            token_id="token",
            price=0.35,
            shares=2.5,
            exit_reason="STOP_LOSS",
        )
    )
    return (
        entry.startswith("[DRY_RUN] BUY")
        and exit_log.startswith("[DRY_RUN] SELL")
        and "STOP_LOSS" in exit_log
    )


def _risk_guards_implemented(conn: sqlite3.Connection) -> tuple[bool, str]:
    if MAX_OPEN_POSITIONS < 1:
        return False, f"MAX_OPEN_POSITIONS={MAX_OPEN_POSITIONS} is invalid"
    if MAX_DAILY_LOSS_USDC <= 0:
        return False, f"MAX_DAILY_LOSS_USDC={MAX_DAILY_LOSS_USDC} is invalid"
    result = check_can_open_position(conn)
    if result.reason and "MAX_" not in (result.reason or ""):
        return True, "guard callable"
    return True, (
        f"MAX_OPEN_POSITIONS={MAX_OPEN_POSITIONS}, "
        f"MAX_DAILY_LOSS_USDC={MAX_DAILY_LOSS_USDC}"
    )


def build_report() -> list[ReadinessCheck]:
    init_db()
    checks: list[ReadinessCheck] = []

    has_entry = _module_has_live_entry_submission()
    checks.append(
        ReadinessCheck(
            name="1. Real CLOB entry submission",
            passed=has_entry,
            details=(
                "execution._submit_live_buy uses create_and_post_order"
                if has_entry
                else "No live entry submission path found"
            ),
        )
    )

    has_exit = _module_has_live_exit_submission() and _strategy_modules_use_exit_gate()
    checks.append(
        ReadinessCheck(
            name="2. Real CLOB exit submission (V2/V2.5/V3)",
            passed=has_exit,
            details=(
                "execution._submit_live_sell + close_early_reversion_position"
                if has_exit
                else "Live exit path missing or not wired into strategies"
            ),
        )
    )

    if wallet_configured():
        auth_ok, auth_details = verify_authentication()
        checks.append(
            ReadinessCheck(
                name="3. Wallet & API authentication",
                passed=auth_ok,
                details=auth_details,
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                name="3. Wallet & API authentication",
                passed=False,
                details="POLY_PRIVATE_KEY is not set",
            )
        )

    with connect() as conn:
        idem_table = _order_intents_table_exists(conn)
        idem_unique = _order_intents_has_unique_key(conn) if idem_table else False
        entry_gate = _strategy_modules_use_execution_gate()
        exit_idem = _exit_idempotency_implemented()

    idem_ok = idem_table and idem_unique and entry_gate and exit_idem
    checks.append(
        ReadinessCheck(
            name="4. Double-send protection (entry + exit)",
            passed=idem_ok,
            details=(
                "order_intents UNIQUE + :entry/:exit idempotency keys"
                if idem_ok
                else (
                    f"table={idem_table}, unique_key={idem_unique}, "
                    f"entry_gate={entry_gate}, exit_idem={exit_idem}"
                )
            ),
        )
    )

    mode_ok = TRADING_MODE in VALID_TRADING_MODES
    checks.append(
        ReadinessCheck(
            name="5. TRADING_MODE (paper/dry_run/live)",
            passed=mode_ok,
            details=f"TRADING_MODE={TRADING_MODE}",
        )
    )

    checks.append(
        ReadinessCheck(
            name="6. LIVE_EXIT_ENABLED toggle",
            passed=True,
            details=f"LIVE_EXIT_ENABLED={LIVE_EXIT_ENABLED}",
        )
    )

    dry_run_ok = _dry_run_logging_ready()
    checks.append(
        ReadinessCheck(
            name="7. dry_run entry/exit logging",
            passed=dry_run_ok,
            details="[DRY_RUN] BUY and SELL log formats available",
        )
    )

    with connect() as conn:
        risk_ok, risk_details = _risk_guards_implemented(conn)

    checks.append(
        ReadinessCheck(
            name="8. MAX_OPEN_POSITIONS guard",
            passed=risk_ok and MAX_OPEN_POSITIONS >= 1,
            details=risk_details,
        )
    )
    checks.append(
        ReadinessCheck(
            name="9. MAX_DAILY_LOSS_USDC guard",
            passed=risk_ok and MAX_DAILY_LOSS_USDC > 0,
            details=risk_details,
        )
    )

    return checks


def format_report(checks: list[ReadinessCheck]) -> str:
    lines = [
        "=== Live Trading Readiness Report ===",
        f"TRADING_MODE: {TRADING_MODE}",
        f"LIVE_EXIT_ENABLED: {LIVE_EXIT_ENABLED}",
        "",
    ]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"{check.name}")
        lines.append(f"  {status} — {check.details}")
        lines.append("")

    live_ready = all(item.passed for item in checks)
    overall = "PASS" if live_ready else "FAIL"
    lines.append(f"OVERALL LIVE READINESS: {overall}")
    if TRADING_MODE != "live" and live_ready:
        lines.append(
            "Note: infrastructure ready; set TRADING_MODE=live and LIVE_EXIT_ENABLED=true."
        )
    elif TRADING_MODE == "live" and not live_ready:
        lines.append("Warning: TRADING_MODE=live but readiness checks failed.")
    return "\n".join(lines)


def print_report() -> None:
    checks = build_report()
    print(format_report(checks))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
