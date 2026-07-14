"""Phase G.4 — per-symbol threshold optimizer v2 (learned, not global brute force)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

CONF_GRID = (7.8, 7.5, 7.2, 7.0, 6.8, 6.5)
SCORE_GRID = (72, 68, 65, 60, 55)
LIQ_GRID = (0.75, 0.70, 0.65, 0.60)
RR_GRID = (3.0, 2.5, 2.2, 2.0)


@dataclass(frozen=True)
class SymbolThresholdG42:
    symbol: str
    min_confidence: float
    min_market_score: float
    min_liquidity: float
    min_rr: float
    win_rate: float
    profit_factor: float
    sample_size: int


def _would_pass(row: Any, *, conf: float, score: float, liq: float, rr: float) -> bool:
    if float(row["confidence"] or 0) < conf:
        return False
    if float(row["market_score"] or 0) < score:
        return False
    if float(row["liquidity_score"] or 0) < liq:
        return False
    if float(row["rr"] or 0) < rr:
        return False
    return True


def _is_win(row: Any) -> bool:
    if row["is_win"]:
        return True
    return float(row["pnl_pct"] or 0) > 0


def _pnl(row: Any) -> float:
    return float(row["pnl_pct"] or 0)


def _load_validation_rows(conn: Any, *, days: int) -> list[Any]:
    since = int(time.time()) - days * 86400
    return conn.execute(
        """
        SELECT symbol, confidence, market_score, liquidity_score, rr, is_win, pnl_pct
        FROM market_validation_records_g4
        WHERE created_at >= ? AND confidence IS NOT NULL AND market_score IS NOT NULL
        """,
        (since,),
    ).fetchall()


def run_threshold_optimizer_g42(conn: Any, *, days: int = 30) -> list[SymbolThresholdG42]:
    rows = _load_validation_rows(conn, days=days)
    if not rows:
        return []

    by_symbol: dict[str, list[Any]] = {}
    for r in rows:
        by_symbol.setdefault(str(r["symbol"]), []).append(r)

    results: list[SymbolThresholdG42] = []
    for symbol, sym_rows in sorted(by_symbol.items()):
        best: SymbolThresholdG42 | None = None
        for conf in CONF_GRID:
            for score in SCORE_GRID:
                for liq in LIQ_GRID:
                    for rr in RR_GRID:
                        passed = [
                            r for r in sym_rows
                            if _would_pass(r, conf=conf, score=score, liq=liq, rr=rr)
                        ]
                        if len(passed) < 3:
                            continue
                        wins = sum(1 for r in passed if _is_win(r))
                        win_rate = wins / len(passed)
                        gross_win = sum(max(0.0, _pnl(r)) for r in passed)
                        gross_loss = sum(abs(min(0.0, _pnl(r))) for r in passed)
                        pf = gross_win / gross_loss if gross_loss > 0 else (gross_win if gross_win > 0 else 0.0)
                        candidate = SymbolThresholdG42(
                            symbol=symbol,
                            min_confidence=conf,
                            min_market_score=score,
                            min_liquidity=liq,
                            min_rr=rr,
                            win_rate=round(win_rate, 3),
                            profit_factor=round(pf, 2),
                            sample_size=len(passed),
                        )
                        if best is None or (candidate.profit_factor, candidate.win_rate) > (
                            best.profit_factor, best.win_rate,
                        ):
                            best = candidate
        if best:
            results.append(best)
    return sorted(results, key=lambda s: (-s.profit_factor, -s.win_rate))


def persist_optimizer_g42(conn: Any, *, report_date: str, scenarios: list[SymbolThresholdG42]) -> int:
    now = int(time.time())
    n = 0
    for s in scenarios:
        conn.execute(
            """
            INSERT INTO market_validation_optimizer_g4 (
              report_date, symbol, min_confidence, min_market_score, min_liquidity, min_rr,
              win_rate, profit_factor, sample_size, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date, symbol) DO UPDATE SET
              min_confidence = excluded.min_confidence,
              min_market_score = excluded.min_market_score,
              min_liquidity = excluded.min_liquidity,
              min_rr = excluded.min_rr,
              win_rate = excluded.win_rate,
              profit_factor = excluded.profit_factor,
              sample_size = excluded.sample_size,
              created_at = excluded.created_at
            """,
            (
                report_date, s.symbol, s.min_confidence, s.min_market_score,
                s.min_liquidity, s.min_rr, s.win_rate, s.profit_factor,
                s.sample_size, now,
            ),
        )
        n += 1
    return n


def format_optimizer_report_g42(conn: Any, *, days: int = 30) -> str:
    scenarios = run_threshold_optimizer_g42(conn, days=days)
    if not scenarios:
        return "G4 Optimizer v2 — insufficient validation data. Run validation cycle first."

    lines = [
        f"G4 Threshold Optimizer v2 — per symbol (last {days} days)",
        "",
        "Learned thresholds (advisory only — not auto-applied)",
        "",
    ]
    for s in scenarios:
        lines.extend([
            str(s.symbol),
            "",
            f"Conf ≥{s.min_confidence}  Score ≥{s.min_market_score}  "
            f"Liq ≥{s.min_liquidity:.2f}  RR ≥{s.min_rr}",
            "",
            "Win Rate",
            f"{s.win_rate * 100:.0f}%",
            "",
            "Profit Factor",
            f"{s.profit_factor:.2f}",
            "",
            f"n={s.sample_size}",
            "",
            "------------",
            "",
        ])
    return "\n".join(lines).rstrip()
