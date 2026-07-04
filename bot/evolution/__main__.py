"""CLI: python -m bot.evolution <command>

Commands:
  status                Show active experiments
  cancel-shadow        Cancel active shadow (requires --id and --reason)
  create-shadow        Create validated shadow (requires --parameter, --from-value, --to-value, --source)
"""

from __future__ import annotations

import argparse
import json
import sys


def _print_observe_stats(conn, stats: dict) -> None:
    if not stats:
        print("  Observe hook: no runtime stats recorded yet")
        return
    row = stats.get("bot.main") or next(iter(stats.values()), None)
    if not row:
        print("  Observe hook: no runtime stats recorded yet")
        return
    print(f"  Observe last run: {row.get('last_run_at') or 'never'}")
    print(f"  Observe last success: {row.get('last_success_at') or 'never'}")
    print(f"  Observe total param evals: {row.get('total_parameter_evals', 0)}")
    print(f"  Observe total regime evals: {row.get('total_regime_evals', 0)}")
    if row.get("last_error"):
        print(f"  Observe last error: {row['last_error']} ({row.get('last_error_at')})")
        print(f"  Observe error count: {row.get('error_count', 0)}")
    else:
        print("  Observe last error: none")


def _status() -> int:
    from bot.database import connect, init_db
    from bot.evolution.observe_stats import load_observe_stats
    from bot.evolution.regime_shadow import (
        count_regime_shadow_trades,
        get_latest_regime_shadow,
        get_running_regime_shadow,
    )
    from bot.evolution.shadow_db import (
        count_eligible_trades_after_shadow,
        count_shadow_evaluations,
        get_latest_shadow,
        get_running_shadow,
        last_shadow_evaluation_at,
    )

    init_db()
    with connect() as conn:
        running = get_running_shadow(conn)
        latest = get_latest_shadow(conn)
        observe_stats = load_observe_stats(conn)

        print("=== PARAMETER SHADOW ===")
        if running:
            sid = int(running["id"])
            raw_rows = count_shadow_evaluations(conn, sid)
            eligible = count_eligible_trades_after_shadow(conn, sid)
            print(f"  Status: RUNNING")
            print(f"  ID: {running['id']}")
            print(f"  Parameter: {running['parameter']}")
            print(f"  From: {running['current_value']} → To: {running['shadow_value']}")
            print(f"  Progress: {running.get('sample_size', 0)} / {running.get('target_sample_size', 200)}")
            print(f"  Raw evaluation rows: {raw_rows}")
            print(f"  Eligible closed trades (forward): {eligible}")
            print(f"  Last evaluation: {last_shadow_evaluation_at(conn, sid) or 'none'}")
            print(f"  Created: {running['created_at']}")
            print(f"  Created by: {running.get('created_by') or 'unknown'}")
            print(f"  Creator decision: {running.get('creator_decision') or 'unknown'}")
            if running.get("creator_confidence") is not None:
                print(f"  Creator confidence: {running['creator_confidence']:.0f}%")
            if running.get("creator_reason"):
                print(f"  Creator reason: {running['creator_reason'][:120]}")
        elif latest:
            sid = int(latest["id"])
            print(f"  Status: {latest['status']} (verdict: {latest.get('verdict', 'N/A')})")
            print(f"  ID: {latest['id']}")
            print(f"  Parameter: {latest['parameter']}")
            print(f"  From: {latest['current_value']} → To: {latest['shadow_value']}")
            print(f"  Raw evaluation rows: {count_shadow_evaluations(conn, sid)}")
            print(f"  Created by: {latest.get('created_by') or 'unknown'}")
        else:
            print("  No parameter shadow experiments.")
        print()
        _print_observe_stats(conn, observe_stats)
        print()

        regime = get_running_regime_shadow(conn) or get_latest_regime_shadow(conn)
        print("=== REGIME SHADOW ===")
        if regime:
            from bot.evolution.regime_shadow import (
                last_regime_shadow_eval_at,
                regime_shadow_funnel,
            )

            rid = int(regime["id"])
            regimes = json.loads(regime["regimes_json"])
            raw_rows = count_regime_shadow_trades(conn, rid)
            funnel = regime_shadow_funnel(conn, rid)
            observe_row = observe_stats.get("bot.main") or {}

            print(f"  Status: {regime['status']}")
            print(f"  ID: {regime['id']}")
            print(f"  Filter: {regime['filter_name']}")
            print(f"  Regimes: {', '.join(regimes)}")
            print(f"  Progress: {regime.get('sample_size', 0)} / {regime.get('target_sample_size', 200)}")
            print(f"  Forward ER v2 trades: {funnel['forward_trades']}")
            print(f"  Missing trade_features: {funnel['missing_trade_features']}")
            print(f"  Matching feature rows: {funnel['matching_feature_rows']}")
            print(f"  Non-null regime labels: {funnel['non_null_regime_labels']}")
            print(f"  Normalized valid: {funnel['normalized_valid']}")
            print(f"  Target-regime matches: {funnel['target_regime_matches']}")
            print(f"  Raw evaluation rows: {raw_rows}")
            print(f"  Last evaluation: {last_regime_shadow_eval_at(conn, rid) or 'none'}")
            print(f"  Last observe error: {observe_row.get('last_error') or 'none'}")
            print(f"  Created: {regime['created_at']}")
            print(f"  Created by: {regime.get('created_by') or 'unknown'}")
            print(f"  Creator decision: {regime.get('creator_decision') or 'unknown'}")
        else:
            print("  No regime shadow experiments.")
    return 0


def _diagnose_regime() -> int:
    from bot.database import connect, init_db
    from bot.evolution.regime_shadow import (
        get_running_regime_shadow,
        list_forward_trade_diagnostics,
        regime_shadow_funnel,
    )

    init_db()
    with connect() as conn:
        running = get_running_regime_shadow(conn)
        if not running:
            print("No running regime shadow experiment.")
            return 1

        rid = int(running["id"])
        funnel = regime_shadow_funnel(conn, rid)
        print("=== REGIME SHADOW FUNNEL ===")
        print(f"  forward ER v2 closed:        {funnel['forward_trades']}")
        print(f"  missing trade_features:      {funnel['missing_trade_features']}")
        print(f"  → matching feature rows:     {funnel['matching_feature_rows']}")
        print(f"  → non-null regime_label:     {funnel['non_null_regime_labels']}")
        print(f"  → normalized valid:          {funnel['normalized_valid']}")
        print(f"  → target-regime matches:     {funnel['target_regime_matches']}")
        print(f"  → inserted evaluations:      {funnel['raw_evaluation_rows']}")
        print()
        print("=== FORWARD TRADES ===")
        print(
            f"{'id':>5} {'slug':<26} {'strat':<6} {'side':<4} "
            f"{'feat':<5} {'regime':<16} {'norm':<16} {'filter'}"
        )
        for t in list_forward_trade_diagnostics(conn, rid):
            slug = (t["market_slug"] or "")[:26]
            print(
                f"{t['trade_id']:>5} {slug:<26} {t['strategy_name']:<6} {t['side']:<4} "
                f"{'YES' if t['has_trade_features'] else 'NO':<5} "
                f"{str(t['regime_label'] or '-'):<16} "
                f"{str(t['normalized_regime_label'] or '-'):<16} "
                f"{t['in_target_regime']}"
            )
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
    from bot.evolution.shadow_db import create_shadow_experiment, get_running_shadow

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
            created_by=args.source,
            creator_decision="MANUAL_CLI",
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
    sub.add_parser("diagnose-regime", help="Read-only regime shadow funnel + per-trade detail")

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
    elif args.command == "diagnose-regime":
        return _diagnose_regime()
    elif args.command == "cancel-shadow":
        return _cancel_shadow(args)
    elif args.command == "create-shadow":
        return _create_shadow(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
