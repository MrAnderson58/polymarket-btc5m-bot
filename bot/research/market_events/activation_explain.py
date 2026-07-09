"""Activation tier promotion rules — explain without loosening thresholds."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.activation_rules import DEFAULT_RULES, ActivationRules


def explain_activation_rules(rules: ActivationRules = DEFAULT_RULES) -> str:
    r = rules
    lines = [
        "ACTIVATION RULES (fixed, not optimized)",
        "",
        "Tiers: PAPER_ACTIVE | WATCH | INACTIVE",
        "",
        "=== WATCH (observe_enabled=1, paper_enabled=0) ===",
        f"  turnover_24h >= ${r.min_turnover_watch_usd:,.0f}",
        f"  valid indexPrice (reference_provider=BYBIT_INDEX)",
        f"  |basis_bps| <= {r.max_basis_bps_sanity}",
        f"  quote_age <= {r.max_quote_age_sec}s",
        "",
        "=== PAPER_ACTIVE (additional requirements) ===",
        f"  turnover_24h >= ${r.min_turnover_paper_usd:,.0f}",
        f"  spread_bps <= {r.max_spread_bps_paper}",
        f"  observation_poll_count >= {r.min_observation_polls} (incremented on instrument-discover)",
        f"  instrument-discover --enable-tradfi must be set at promotion time",
        f"  EQUITY/INDEX/ETF: session must be US_REGULAR when --enable-tradfi runs",
        "  COMMODITY: no US session gate (24h commodity perps)",
        "",
        "=== liquidity_tier (LIQUID) vs activation_tier ===",
        "  LIQUID = turnover >= 2x watch minimum ($2M TradFi)",
        "  LIQUID alone does NOT promote to PAPER_ACTIVE",
        "",
        "=== Why XAU/CL/XAG became PAPER_ACTIVE ===",
        "  asset_class=COMMODITY → no US_REGULAR session gate",
        "  turnover >> $5M, spread tight, obs_polls >= 2 after 2nd discover, --enable-tradfi",
        "",
        "=== Why NVDA may stay WATCH despite liq=LIQUID ===",
        f"  EQUITY requires US_REGULAR session during discover --enable-tradfi",
        f"  OR turnover_24h < ${r.min_turnover_paper_usd:,.0f} at discover time",
        f"  OR spread_bps > {r.max_spread_bps_paper} at discover time",
        f"  OR observation_poll_count < {r.min_observation_polls}",
        "",
        "=== Why QQQ may stay WATCH despite liq=LIQUID ===",
        f"  ETF turnover often < ${r.min_turnover_paper_usd:,.0f} (paper gate, not watch gate)",
        "  LIQUID tier only means turnover >= $2M, not paper threshold $5M",
    ]
    return "\n".join(lines)


def explain_instrument_activation(conn: Any, *, canonical: str | None = None) -> str:
    rules = DEFAULT_RULES
    lines = [explain_activation_rules(rules), "", "=== REGISTRY STATE ==="]
    if canonical:
        rows = conn.execute(
            "SELECT * FROM market_events_instruments WHERE canonical_asset = ? OR venue_symbol = ?",
            (canonical.upper(), canonical.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM market_events_instruments
            WHERE asset_class != 'CRYPTO'
            ORDER BY activation_tier DESC, canonical_asset
            """,
        ).fetchall()
    for row in rows:
        meta = json.loads(row["metadata_json"] or "{}")
        metrics = meta.get("activation_metrics") or {}
        reasons = meta.get("activation_reasons") or []
        obs_polls = meta.get("observation_poll_count", metrics.get("observation_poll_count", "?"))
        lines.extend([
            "",
            f"  {row['canonical_asset']} ({row['venue_symbol']})",
            f"    activation_tier={row['activation_tier']} liquidity_tier={row['liquidity_tier']}",
            f"    observe={row['observe_enabled']} paper={row['paper_enabled']}",
            f"    observation_poll_count={obs_polls}",
            f"    last_metrics: turnover={metrics.get('turnover_24h')} spread_bps={metrics.get('spread_bps')} "
            f"basis_bps={metrics.get('basis_bps')} session={metrics.get('session_regime')}",
            f"    reasons: {', '.join(reasons) if reasons else '(none stored)'}",
        ])
        if row["activation_tier"] == "WATCH" and row["liquidity_tier"] == "LIQUID":
            turn = metrics.get("turnover_24h")
            if turn is not None and turn < rules.min_turnover_paper_usd:
                lines.append(f"    → WATCH: turnover ${turn:,.0f} < paper gate ${rules.min_turnover_paper_usd:,.0f}")
            elif obs_polls != "?" and int(obs_polls) < rules.min_observation_polls:
                lines.append(f"    → WATCH: observation_poll_count={obs_polls} < {rules.min_observation_polls}")
            elif "session_not_us_regular" in str(reasons):
                lines.append("    → WATCH: discover --enable-tradfi ran outside US_REGULAR")
            elif "paper_eligible_but_enable_tradfi_not_set" in str(reasons):
                lines.append("    → WATCH: rules passed but --enable-tradfi not set on last discover")
    return "\n".join(lines)
