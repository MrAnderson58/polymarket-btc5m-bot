"""Recommendation engine (Blocks 8-9)."""

from __future__ import annotations

from typing import Any

from bot.optimizer.constants import CONFIDENCE_HIGH_N, CONFIDENCE_MEDIUM_N


def confidence_label(n: int) -> str:
    if n >= CONFIDENCE_HIGH_N:
        return "High confidence"
    if n >= CONFIDENCE_MEDIUM_N:
        return "Medium confidence"
    return f"Low confidence (n={n})"


def confidence_pct(n: int) -> float:
    if n >= CONFIDENCE_HIGH_N:
        return 94.0
    if n >= CONFIDENCE_MEDIUM_N:
        return 72.0
    return max(25.0, min(55.0, n * 3))


def build_recommendations(report: dict[str, Any]) -> list[dict[str, Any]]:
    recs: list[tuple[float, dict[str, Any]]] = []
    grid = report.get("parameter_optimizer", {})
    cur = grid.get("current", {})
    opt = grid.get("optimal", {})
    n = int(opt.get("trades", 0))
    imp_pct = grid.get("expected_improvement_pct", 0)

    if opt.get("stop_pct") != cur.get("stop_pct") and n >= 30:
        recs.append(
            (
                confidence_pct(n),
                {
                    "parameter": "STOP",
                    "value": f"{abs(opt['stop_pct']):.0f}%",
                    "confidence": confidence_label(n),
                    "confidence_pct": confidence_pct(n),
                    "message": (
                        f"Если изменить стоп на {abs(opt['stop_pct']):.0f}%, "
                        f"ожидаемое улучшение {imp_pct:+.1f}%"
                    ),
                },
            )
        )

    if opt.get("entry") != cur.get("entry") and n >= 30:
        recs.append(
            (
                confidence_pct(n) - 5,
                {
                    "parameter": "Entry",
                    "value": f"{opt['entry']:.2f}",
                    "confidence": confidence_label(n),
                    "confidence_pct": confidence_pct(n) - 5,
                    "message": f"Снизить max entry до {opt['entry']:.2f}",
                },
            )
        )

    if opt.get("trailing_activation") != cur.get("trailing_activation"):
        recs.append(
            (
                confidence_pct(n) - 8,
                {
                    "parameter": "Trailing activation",
                    "value": f"{opt['trailing_activation']:.3f}",
                    "confidence": confidence_label(n),
                    "confidence_pct": confidence_pct(n) - 8,
                    "message": f"Trailing activation → {opt['trailing_activation']:.3f}",
                },
            )
        )

    if cur.get("entry") == 0.40 and opt.get("entry", 0.4) < 0.40:
        recs.append(
            (
                88.0,
                {
                    "parameter": "Disable entry 0.40",
                    "value": "yes",
                    "confidence": "High confidence",
                    "confidence_pct": 88.0,
                    "message": "Entry 0.40 показывает худший PF в истории",
                },
            )
        )

    btc_f = opt.get("btc_filter_usd")
    if btc_f is not None and btc_f < 30:
        recs.append(
            (
                75.0,
                {
                    "parameter": "BTC filter",
                    "value": f"skip when BTC 30s > +{btc_f:.0f}",
                    "confidence": "Medium confidence",
                    "confidence_pct": 75.0,
                    "message": f"Не входить при BTC move 30s > +{btc_f:.0f}",
                },
            )
        )

    for rule in report.get("rules_discovery", [])[:2]:
        if rule.get("target") == "is_stop" and rule.get("rate", 0) >= 0.7:
            recs.append(
                (
                    70.0,
                    {
                        "parameter": "Rule",
                        "value": rule.get("rule_text", ""),
                        "confidence": "Medium confidence",
                        "confidence_pct": 70.0,
                        "message": rule.get("rule_text", ""),
                    },
                )
            )

    wf = report.get("walk_forward", [])
    if wf and not all(w.get("generalizes") for w in wf):
        recs.append(
            (
                90.0,
                {
                    "parameter": "Action",
                    "value": "DO NOTHING",
                    "confidence": "High confidence",
                    "confidence_pct": 90.0,
                    "message": "Walk-forward не подтверждает оптимальные параметры — не менять live",
                },
            )
        )

    if not recs:
        recs.append(
            (
                80.0,
                {
                    "parameter": "Action",
                    "value": "DO NOTHING",
                    "confidence": "High confidence",
                    "confidence_pct": 80.0,
                    "message": "Накопить ещё 200–300 сделок перед изменением параметров",
                },
            )
        )

    recs.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in recs[:5]]
