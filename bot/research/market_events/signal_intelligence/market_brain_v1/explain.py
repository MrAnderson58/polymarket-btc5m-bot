"""Explainability for Adaptive Market Brain decisions."""

from __future__ import annotations

from typing import Any


def explain_decision(
    fusion: dict[str, Any],
    opinions: list[dict[str, Any]],
    *,
    trade: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = fusion.get("decision")
    lines: list[str] = []
    bullets: list[str] = []

    if decision == "NO_TRADE":
        lines.append("NO TRADE because module conflict is HIGH.")
        for m in (fusion.get("conflict") or {}).get("strong_modules") or []:
            bullets.append(f"{m.get('module')} says {m.get('direction')} (conf={m.get('confidence')})")
    else:
        lines.append(
            f"{decision} with calibrated-ready confidence {fusion.get('confidence_raw')} "
            f"(P(buy)={fusion.get('probability_buy')}, EV={fusion.get('expected_ev')}, "
            f"PF≈{fusion.get('expected_pf')}, risk={fusion.get('risk')})."
        )
        # Top supporting modules
        supporting = [
            o for o in opinions
            if o.get("direction") == fusion.get("direction")
            or (decision == "HOLD" and o.get("direction") == "HOLD")
        ]
        supporting.sort(
            key=lambda o: -float(o.get("confidence") or 0) * float(o.get("weight") or 0)
        )
        for o in supporting[:5]:
            why = "; ".join(o.get("reasons") or []) or "signal"
            bullets.append(
                f"{o['module']}: {o['direction']} conf={o['confidence']} edge={o['edge']} ({why})"
            )
        # Context
        if trade:
            if trade.get("regime"):
                bullets.append(f"regime={trade.get('regime')}")
            if trade.get("gate_decision") or trade.get("gate"):
                bullets.append(f"gate={trade.get('gate_decision') or trade.get('gate')}")

    # Dissenters
    dissent = [
        o for o in opinions
        if o.get("direction") in ("BUY", "SELL")
        and fusion.get("direction") in ("BUY", "SELL")
        and o.get("direction") != fusion.get("direction")
    ]
    dissent_txt = [
        f"{o['module']} {o['direction']} conf={o['confidence']}" for o in dissent[:4]
    ]

    return {
        "headline": lines[0] if lines else str(decision),
        "because": bullets,
        "dissent": dissent_txt,
        "votes": fusion.get("votes"),
        "conflict_level": (fusion.get("conflict") or {}).get("level"),
        "text": "\n".join(
            lines
            + ["Because:"]
            + [f"- {b}" for b in bullets]
            + (["Dissent:"] + [f"- {d}" for d in dissent_txt] if dissent_txt else [])
        ),
    }


__all__ = ["explain_decision"]
