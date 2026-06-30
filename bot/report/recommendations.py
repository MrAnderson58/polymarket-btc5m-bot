"""Rule-based recommendations from report data."""

from __future__ import annotations

from typing import Any


def generate_recommendations(report: dict[str, Any]) -> list[str]:
    recs: list[str] = []

    positions = report.get("current_positions", {})
    for warning in positions.get("warnings", []):
        recs.append(
            f"Trade {warning['trade_id']}: {warning['message']}"
        )
    if positions.get("failed_exit_intents", 0) > 0:
        recs.append(
            f"Есть {positions['failed_exit_intents']} failed exit intent(s) — проверить recovery."
        )
    if positions.get("orphan_intents", 0) > 0:
        recs.append(
            f"Есть {positions['orphan_intents']} orphan intent(s) без открытой сделки."
        )

    entry = report.get("entry_price_analysis", {})
    best = entry.get("best_entry_price")
    worst = entry.get("worst_entry_price")
    if best is not None and worst is not None and best != worst:
        recs.append(
            f"Entry {worst:.2f} показывает худший PF — рассмотреть фокус на {best:.2f}."
        )

    short = report.get("stop_loss_short_recovery", {})
    if short:
        recovered_60 = short.get("recovered_entry_60s_rate", 0)
        never_60 = short.get("never_recovered_60s_rate", 0)
        if recovered_60 >= 0.35:
            recs.append(
                f"{recovered_60:.0%} STOP_LOSS восстанавливаются до entry за 60s — "
                "стоп может быть слишком агрессивным."
            )
        if never_60 >= 0.55:
            recs.append(
                f"{never_60:.0%} стопов не восстанавливаются за 60s — "
                "удержание после стопа вряд ли улучшит результат."
            )

    alt_stop = report.get("alternative_stop_test", {})
    actual = report.get("stop_loss_analysis", {})
    if alt_stop and actual:
        base_key = str(-18.0)
        if base_key in alt_stop:
            wider = alt_stop[base_key]
            current_sl = report.get("configuration", {}).get("stop_loss_pct", -10)
            if wider.get("net_profit", 0) > alt_stop.get(str(float(current_sl)), {}).get("net_profit", -9999):
                recs.append(
                    "Stop Loss -18% показывает лучший Net в offline-тесте — "
                    "рассмотреть расширение стопа."
                )

    btc_dir = report.get("btc_direction", {})
    for strategy, buckets in btc_dir.items():
        by_name = {b["direction"]: b for b in buckets}
        up = by_name.get("BTC UP", {})
        down = by_name.get("BTC DOWN", {})
        if up.get("trades", 0) >= 5 and up.get("avg_pnl", 0) < 0:
            recs.append(f"{strategy}: BTC UP даёт отрицательное ожидание.")
        if down.get("trades", 0) >= 5 and down.get("avg_pnl", 0) > 5:
            recs.append(
                f"{strategy}: BTC DOWN показывает сильный avg PnL "
                f"({down['avg_pnl']:+.1f}%)."
            )

    trail_sim = report.get("trailing_simulation", {})
    best = trail_sim.get("best")
    if best:
        recs.append(
            f"Trailing offline: activation={best['activation']:.3f}, "
            f"distance={best['distance']:.3f} даёт лучший Net "
            f"({best['net_profit']:+.0f}%)."
        )

    walk = report.get("walk_forward", {})
    trend = walk.get("trend")
    if trend == "degrading":
        recs.append("Walk-forward: стратегия деградирует на последних сделках.")
    elif trend == "improving":
        recs.append("Walk-forward: стратегия улучшается на последних сделках.")

    exec_q = report.get("execution_quality", {})
    if exec_q.get("avg_buy_slippage", 0) > 0.01:
        recs.append("Average buy slippage увеличился — проверить execution quality.")

    if not recs:
        recs.append("Критических проблем не обнаружено. Продолжать мониторинг.")
    return recs
