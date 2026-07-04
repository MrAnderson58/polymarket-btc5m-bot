"""CLI: python -m bot.evolution <command>

Commands:
  status                Show active experiments
  cancel-shadow        Cancel active shadow (requires --id and --reason)
  create-shadow        Create validated shadow (requires --parameter, --from-value, --to-value, --source)
"""

from __future__ import annotations

import argparse
import sys


def _status() -> int:
    from bot.database import connect, init_db
    from bot.evolution.shadow_db import get_latest_shadow, get_running_shadow
    from bot.evolution.regime_shadow import get_running_regime_shadow, get_latest_regime_shadow

    init_db()
    with connect() as conn:
        running = get_running_shadow(conn)
        latest = get_latest_shadow(conn)

        print("=== PARAMETER SHADOW ===")
        if running:
            print(f"  Status: RUNNING")
            print(f"  ID: {running['id']}")
            print(f"  Parameter: {running['parameter']}")
            print(f"  From: {running['current_value']} → To: {running['shadow_value']}")
            print(f"  Progress: {running.get('sample_size', 0)} / {running.get('target_sample_size', 200)}")
            print(f"  Created: {running['created_at']}")
        elif latest:
            print(f"  Status: {latest['status']} (verdict: {latest.get('verdict', 'N/A')})")
            print(f"  ID: {latest['id']}")
            print(f"  Parameter: {latest['parameter']}")
            print(f"  From: {latest['current_value']} → To: {latest['shadow_value']}")
        else:
            print("  No parameter shadow experiments.")
        print()

        regime = get_running_regime_shadow(conn) or get_latest_regime_shadow(conn)
        print("=== REGIME SHADOW ===")
        if regime:
            import json
            regimes = json.loads(regime["regimes_json"])
            print(f"  Status: {regime['status']}")
            print(f"  ID: {regime['id']}")
            print(f"  Filter: {regime['filter_name']}")
            print(f"  Regimes: {', '.join(regimes)}")
            print(f"  Progress: {regime.get('sample_size', 0)} / {regime.get('target_sample_size', 200)}")
            print(f"  Created: {regime['created_at']}")
        else:
            print("  No regime shadow experiments.")
    return 0


def _cancel_shadow(args: argparse.Namespace) -> int:
    from bot.database import connect, init_db
    from bot.evolution.shadow_db import get_running_shadow

    if not args.id or not args.reason:
        print("ERROR: --id and --reason are required for cancel-shadow")
        return 1

    init_db()
    with connect() as conn:
        running = get_running_shadow(conn)
        if not running:
            print("No active shadow experiment to cancel.")
            return 0
        if int(running["id"]) != args.id:
            print(f"ERROR: Active shadow ID is {running['id']}, not {args.id}")
            return 1

        try:
            conn.execute(
                """
                UPDATE evolution_shadow
                SET status = 'CANCELLED', completed_at = datetime('now'), verdict = 'CANCELLED'
                WHERE id = ? AND status = 'RUNNING'
                """,
                (args.id,),
            )
        except Exception:
            conn.execute(
                """
                UPDATE evolution_shadow
                SET status = 'COMPLETE', completed_at = datetime('now'), verdict = 'REJECT'
                WHERE id = ? AND status = 'RUNNING'
                """,
                (args.id,),
            )
        conn.commit()
        print(f"Shadow #{args.id} CANCELLED (verdict=REJECT). Reason: {args.reason}")
    return 0


def _create_shadow(args: argparse.Namespace) -> int:
    from bot.database import connect, init_db
    from bot.evolution.shadow_db import get_running_shadow, create_shadow_experiment

    if not all([args.parameter, args.from_value is not None, args.to_value is not None, args.source]):
        print("ERROR: --parameter, --from-value, --to-value, --source are required")
        return 1

    init_db()
    with connect() as conn:
        running = get_running_shadow(conn)
        if running:
            print(f"ERROR: Active shadow #{running['id']} already running.")
            print(f"  Cancel it first: python -m bot.evolution cancel-shadow --id {running['id']} --reason '...'")
            return 1

        exp = create_shadow_experiment(
            conn,
            parameter=args.parameter,
            current_value=args.from_value,
            shadow_value=args.to_value,
        )
        conn.commit()
        print(f"Shadow #{exp['id']} CREATED.")
        print(f"  Parameter: {args.parameter}")
        print(f"  From: {args.from_value} → To: {args.to_value}")
        print(f"  Source: {args.source}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.evolution")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show active experiments")

    cancel = sub.add_parser("cancel-shadow", help="Cancel active shadow experiment")
    cancel.add_argument("--id", type=int, required=True)
    cancel.add_argument("--reason", type=str, required=True)

    create = sub.add_parser("create-shadow", help="Create validated shadow experiment")
    create.add_argument("--parameter", type=str, required=True)
    create.add_argument("--from-value", type=float, required=True)
    create.add_argument("--to-value", type=float, required=True)
    create.add_argument("--source", type=str, required=True)

    args = parser.parse_args()

    if args.command == "status":
        return _status()
    elif args.command == "cancel-shadow":
        return _cancel_shadow(args)
    elif args.command == "create-shadow":
        return _create_shadow(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
