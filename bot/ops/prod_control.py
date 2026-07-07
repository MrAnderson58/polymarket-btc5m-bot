"""Production process start/stop/status — used by scripts/prod-*.sh."""

from __future__ import annotations

import argparse
import sys

from bot.ops.process_utils import (
    find_main_bot_processes,
    find_telegram_poll_processes,
    logs_dir,
    project_python,
    remove_stale_telegram_lock,
    start_detached,
    stop_processes,
)
from bot.research.futures_agent.env_bootstrap import bootstrap_config


def cmd_status() -> int:
    bootstrap_config()
    print(f"python: {project_python()}")
    print(f"logs: {logs_dir()}")
    main_procs = find_main_bot_processes()
    print(f"bot.main: {'RUNNING' if main_procs else 'STOPPED'} ({len(main_procs)} instance(s))")
    for proc in main_procs:
        print(f"  PID {proc.pid}: {proc.command[:200]}")
    if len(main_procs) > 1:
        print("WARNING: duplicate bot.main processes")

    tg_procs = find_telegram_poll_processes()
    print(
        f"telegram-poll: {'RUNNING' if tg_procs else 'STOPPED'} ({len(tg_procs)} instance(s))"
    )
    for proc in tg_procs:
        print(f"  PID {proc.pid}: {proc.command[:200]}")
    if len(tg_procs) > 1:
        print("WARNING: duplicate telegram-poll processes")
        return 2
    if len(main_procs) > 1:
        return 2
    return 0


def cmd_start(*, component: str) -> int:
    bootstrap_config()
    if component in {"main", "all"}:
        existing = find_main_bot_processes()
        if existing:
            print("REFUSE: bot.main already running")
            for proc in existing:
                print(f"  PID {proc.pid}: {proc.command[:200]}")
            return 1
        pid, log_path = start_detached(module_args=["-m", "bot.main"], log_name="bot-main.log")
        print(f"started bot.main PID {pid}")
        print(f"log: {log_path}")

    if component in {"telegram", "all"}:
        existing = find_telegram_poll_processes()
        if existing:
            print("REFUSE: telegram-poll already running")
            for proc in existing:
                print(f"  PID {proc.pid}: {proc.command[:200]}")
            return 1
        removed, msg = remove_stale_telegram_lock()
        if removed and msg != "no lock file present":
            print(f"telegram lock: {msg}")
        pid, log_path = start_detached(
            module_args=["-m", "bot.research.futures_agent", "telegram-poll"],
            log_name="futures-agent-telegram.log",
        )
        print(f"started telegram-poll PID {pid}")
        print(f"log: {log_path}")
    return 0


def cmd_stop(*, component: str) -> int:
    bootstrap_config()
    ok = True
    if component in {"main", "all"}:
        success, lines = stop_processes(find_main_bot_processes(), label="bot.main")
        for line in lines:
            print(line)
        ok = ok and success
    if component in {"telegram", "all"}:
        success, lines = stop_processes(
            find_telegram_poll_processes(), label="telegram-poll"
        )
        for line in lines:
            print(line)
        ok = ok and success
        removed, msg = remove_stale_telegram_lock()
        if removed and "removed" in msg:
            print(f"telegram lock: {msg}")
    return 0 if ok else 1


def cmd_restart(*, component: str) -> int:
    rc = cmd_stop(component=component)
    if rc != 0:
        return rc
    return cmd_start(component=component)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Production process control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    start_p = sub.add_parser("start")
    start_p.add_argument(
        "--component",
        choices=["main", "telegram", "all"],
        default="all",
    )
    stop_p = sub.add_parser("stop")
    stop_p.add_argument(
        "--component",
        choices=["main", "telegram", "all"],
        default="all",
    )
    restart_p = sub.add_parser("restart")
    restart_p.add_argument(
        "--component",
        choices=["main", "telegram", "all"],
        default="all",
    )
    args = parser.parse_args(argv)
    if args.command == "status":
        return cmd_status()
    if args.command == "start":
        return cmd_start(component=args.component)
    if args.command == "stop":
        return cmd_stop(component=args.component)
    if args.command == "restart":
        return cmd_restart(component=args.component)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
