"""Phase G.3.2 — threshold grid optimizer from candidate replay outcomes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

CONF_GRID = (7.5, 7.2, 7.0, 6.8, 6.5)
SCORE_GRID = (70, 65, 60, 55)
RR_GRID = (2.5, 2.2, 2.0)


@dataclass(frozen=True)
class ThresholdScenarioG32:
    min_confidence: float
    min_market_score: float
    min_rr: float
    win_rate: float
    signals_per_day: float
    profit_factor: float
    sample_size: int


def _would_pass(row: Any, *, conf: float, score: float, rr: float) -> bool:
    c_conf = float(row["confidence"] or 0)
    c_score = float(row["market_score"] or 0)
    c_rr = float(row["candidate_rr"] or row["rr"] or 0)
    if c_conf < conf or c_score < score or c_rr < rr:
        return False
    return True


def _is_win(row: Any) -> bool:
    if row["would_hit_tp"]:
        return True
    return float(row["max_profit_pct"] or 0) > 0


def _pnl_for_row(row: Any) -> float:
    return float(row["max_profit_pct"] or 0)


def run_threshold_optimizer_g32(conn: Any, *, days: int = 7) -> list[ThresholdScenarioG32]:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT o.would_hit_tp, o.would_hit_sl, o.max_profit_pct, o.best_rr,
               c.confidence, c.market_score, c.rr AS candidate_rr, c.created_at
        FROM market_candidate_outcomes_g32 o
        JOIN market_candidate_g31 c ON c.id = o.candidate_id
        WHERE o.created_at >= ? AND o.replay_status = 'COMPLETE'
          AND c.confidence IS NOT NULL AND c.market_score IS NOT NULL
        """,
        (since,),
    ).fetchall()

    if not rows:
        rows = conn.execute(
            """
            SELECT o.would_hit_tp, o.would_hit_sl, o.max_profit_pct, o.best_rr,
                   c.confidence, c.market_score, c.rr AS candidate_rr, c.created_at
            FROM market_candidate_outcomes_g32 o
            JOIN market_candidate_g31 c ON c.id = o.candidate_id
            WHERE o.created_at >= ? AND c.confidence IS NOT NULL
            """,
            (since,),
        ).fetchall()

    span_days = max(1.0, days)
    scenarios: list[ThresholdScenarioG32] = []

    for conf in CONF_GRID:
        for score in SCORE_GRID:
            for rr in RR_GRID:
                passed = [r for r in rows if _would_pass(r, conf=conf, score=score, rr=rr)]
                if not passed:
                    scenarios.append(ThresholdScenarioG32(
                        min_confidence=conf, min_market_score=score, min_rr=rr,
                        win_rate=0.0, signals_per_day=0.0, profit_factor=0.0, sample_size=0,
                    ))
                    continue

                wins = sum(1 for r in passed if _is_win(r))
                win_rate = wins / len(passed)
                signals_per_day = len(passed) / span_days

                gross_win = sum(max(0.0, _pnl_for_row(r)) for r in passed)
                gross_loss = sum(abs(min(0.0, _pnl_for_row(r))) for r in passed)
                pf = gross_win / gross_loss if gross_loss > 0 else (gross_win if gross_win > 0 else 0.0)

                scenarios.append(ThresholdScenarioG32(
                    min_confidence=conf,
                    min_market_score=score,
                    min_rr=rr,
                    win_rate=round(win_rate, 3),
                    signals_per_day=round(signals_per_day, 2),
                    profit_factor=round(pf, 2),
                    sample_size=len(passed),
                ))

    return sorted(scenarios, key=lambda s: (s.profit_factor, s.win_rate), reverse=True)


def format_threshold_optimizer_report(conn: Any, *, days: int = 7) -> str:
    scenarios = run_threshold_optimizer_g32(conn, days=days)
    if not scenarios or all(s.sample_size == 0 for s in scenarios):
        return "Insufficient replay data — wait for candidate outcomes to complete."

    lines = [
        f"G3.2 Threshold Optimizer — last {days} days",
        "",
        "Grid: Confidence / Market Score / RR → Expected metrics",
        "",
    ]
    shown = 0
    for s in scenarios:
        if s.sample_size == 0:
            continue
        if shown >= 15:
            break
        lines.extend([
            f"Conf ≥{s.min_confidence}  Score ≥{s.min_market_score}  RR ≥{s.min_rr}",
            "",
            "Expected Win Rate",
            f"{s.win_rate * 100:.0f}%",
            "",
            "Expected Signals/day",
            f"{s.signals_per_day:.1f}",
            "",
            "Expected Profit Factor",
            f"{s.profit_factor:.2f}",
            "",
            f"(n={s.sample_size})",
            "",
            "------------",
            "",
        ])
        shown += 1

    best = next((s for s in scenarios if s.sample_size >= 5), scenarios[0])
    lines.extend([
        "Recommended (best PF with n≥5):",
        f"  Confidence ≥{best.min_confidence}",
        f"  Market Score ≥{best.min_market_score}",
        f"  RR ≥{best.min_rr}",
    ])
    return "\n".join(lines)
