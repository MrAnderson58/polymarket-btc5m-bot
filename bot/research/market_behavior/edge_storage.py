"""Persist multidimensional edge statistics."""

from __future__ import annotations

import sqlite3

from bot.research.market_behavior.config import EDGE_TP_LEVELS, PRIMARY_EV_TP
from bot.research.market_behavior.edge_finder import EdgeCell
from bot.research.market_behavior.schema import EDGE_STATISTICS_TABLE


def store_edge_cells(conn: sqlite3.Connection, cells: list[EdgeCell]) -> None:
    conn.execute(f"DELETE FROM {EDGE_STATISTICS_TABLE}")
    for cell in cells:
        _insert_cell(conn, cell)
    conn.commit()


def _insert_cell(conn: sqlite3.Connection, cell: EdgeCell) -> None:
    tp_cols = {f"tp{int(tp * 100)}_prob": cell.tp_probs.get(tp, 0.0) for tp in EDGE_TP_LEVELS}
    ev_cols = {f"ev_tp{int(tp * 100)}": cell.ev_by_tp.get(tp, 0.0) for tp in EDGE_TP_LEVELS}
    conn.execute(
        f"""
        INSERT INTO {EDGE_STATISTICS_TABLE} (
            direction, entry_bucket, btc_delta_bucket, seconds_left_bucket, spread_bucket,
            samples, tp55_prob, tp60_prob, tp65_prob, tp70_prob, tp75_prob,
            avg_final_delta, avg_move_to_close, avg_spread,
            avg_max_excursion, avg_adverse_excursion, avg_entry_price,
            ev_tp55, ev_tp60, ev_tp65, ev_tp70, ev_tp75,
            expected_profit_tp60, expected_loss_tp60, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            cell.direction,
            cell.entry_bucket,
            cell.btc_delta_bucket,
            cell.seconds_left_bucket,
            cell.spread_bucket,
            cell.samples,
            tp_cols["tp55_prob"],
            tp_cols["tp60_prob"],
            tp_cols["tp65_prob"],
            tp_cols["tp70_prob"],
            tp_cols["tp75_prob"],
            cell.avg_final_delta,
            cell.avg_move_to_close,
            cell.avg_spread,
            cell.avg_max_excursion,
            cell.avg_adverse_excursion,
            cell.avg_entry_price,
            ev_cols["ev_tp55"],
            ev_cols["ev_tp60"],
            ev_cols["ev_tp65"],
            ev_cols["ev_tp70"],
            ev_cols["ev_tp75"],
            cell.expected_profit_by_tp.get(PRIMARY_EV_TP, 0.0),
            cell.expected_loss_by_tp.get(PRIMARY_EV_TP, 0.0),
        ),
    )


def load_edge_cells(conn: sqlite3.Connection) -> list[EdgeCell]:
    rows = conn.execute(
        f"""
        SELECT * FROM {EDGE_STATISTICS_TABLE}
        ORDER BY samples DESC
        """
    ).fetchall()
    cells: list[EdgeCell] = []
    for r in rows:
        cell = EdgeCell(
            direction=r["direction"],
            entry_bucket=r["entry_bucket"],
            btc_delta_bucket=r["btc_delta_bucket"],
            seconds_left_bucket=r["seconds_left_bucket"],
            spread_bucket=r["spread_bucket"],
            samples=int(r["samples"]),
            avg_final_delta=float(r["avg_final_delta"] or 0),
            avg_move_to_close=float(r["avg_move_to_close"] or 0),
            avg_spread=float(r["avg_spread"] or 0),
            avg_max_excursion=float(r["avg_max_excursion"] or 0),
            avg_adverse_excursion=float(r["avg_adverse_excursion"] or 0),
            avg_entry_price=float(r["avg_entry_price"] or 0),
        )
        cell.tp_probs = {
            0.55: float(r["tp55_prob"] or 0),
            0.60: float(r["tp60_prob"] or 0),
            0.65: float(r["tp65_prob"] or 0),
            0.70: float(r["tp70_prob"] or 0),
            0.75: float(r["tp75_prob"] or 0),
        }
        cell.ev_by_tp = {
            0.55: float(r["ev_tp55"] or 0),
            0.60: float(r["ev_tp60"] or 0),
            0.65: float(r["ev_tp65"] or 0),
            0.70: float(r["ev_tp70"] or 0),
            0.75: float(r["ev_tp75"] or 0),
        }
        cell.expected_profit_by_tp[PRIMARY_EV_TP] = float(r["expected_profit_tp60"] or 0)
        cell.expected_loss_by_tp[PRIMARY_EV_TP] = float(r["expected_loss_tp60"] or 0)
        cells.append(cell)
    return cells
