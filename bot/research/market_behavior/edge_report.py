"""Edge report and heatmap rendering."""

from __future__ import annotations

from bot.research.market_behavior.config import EDGE_TP_LEVELS, MIN_EDGE_SAMPLES, PRIMARY_EV_TP
from bot.research.market_behavior.edge_finder import EdgeCell, find_best_edges
from bot.research.market_behavior.quality_control import (
    filter_rankable_cells,
    render_qc_warnings,
    validate_edge_cells,
)


def _entry_display(bucket: str, avg_entry: float) -> str:
    hi = bucket.split("-")[1].replace("c", "")
    try:
        return f"<={int(hi) / 100:.2f}"
    except ValueError:
        return f"~{avg_entry:.2f}"


def _delta_display(bucket: str) -> str:
    if bucket.startswith("<"):
        return f"< {bucket[1:]} $"
    if bucket.startswith(">"):
        return f"> {bucket[1:]} $"
    parts = bucket.split("...")
    if len(parts) == 2:
        return f"{parts[0]}...{parts[1]} $"
    return bucket


def _format_edge_block(cell: EdgeCell, *, tp: float = PRIMARY_EV_TP) -> list[str]:
    lines = [
        cell.direction,
        f"Entry {_entry_display(cell.entry_bucket, cell.avg_entry_price)}",
        f"BTC Delta {_delta_display(cell.btc_delta_bucket)}",
        f"Time {cell.seconds_left_bucket}s",
        f"Spread {cell.spread_bucket}",
        f"Samples {cell.samples}",
    ]
    for level in EDGE_TP_LEVELS:
        prob = cell.tp_probs.get(level, 0.0)
        if level <= tp + 0.10:
            lines.append(f"TP{int(level * 100)} {prob:.0%}")
    exp_p = cell.expected_profit_by_tp.get(tp, 0.0)
    exp_l = cell.expected_loss_by_tp.get(tp, 0.0)
    ev = cell.ev_by_tp.get(tp, 0.0)
    lines.extend([
        f"Expected Profit (TP{int(tp * 100)}): {exp_p:+.4f}",
        f"Expected Loss (TP{int(tp * 100)}): {exp_l:.4f}",
        f"Expected Value: {ev:+.4f}",
        "",
    ])
    return lines


def render_edge_report(cells: list[EdgeCell], *, min_samples: int | None = None, top_n: int = 10) -> str:
    qc = validate_edge_cells(cells, min_samples=min_samples)
    rankable = filter_rankable_cells(cells, qc)
    best = find_best_edges(rankable, min_samples=min_samples, top_n=top_n * 2)
    yes_edges = [c for c in best if c.direction == "YES"][:top_n]
    no_edges = [c for c in best if c.direction == "NO"][:top_n]

    lines = ["BEST HISTORICAL EDGES", "=" * 40, ""]
    if not qc.ok:
        lines.append(render_qc_warnings(qc))
        lines.append("")
    for cell in yes_edges:
        lines.extend(_format_edge_block(cell))
    for cell in no_edges:
        lines.extend(_format_edge_block(cell))

    if not yes_edges and not no_edges:
        lines.append(f"No edges with samples >= {min_samples or MIN_EDGE_SAMPLES}")
    lines.append("Observe-only. No execution impact.")
    return "\n".join(lines)


def render_heatmap_entry_delta(
    cells: list[EdgeCell],
    *,
    direction: str = "YES",
    tp: float = PRIMARY_EV_TP,
    min_samples: int = 10,
) -> str:
    """Entry bucket (rows) x BTC delta bucket (cols) -> TP probability."""
    filtered = [
        c for c in cells
        if c.direction == direction and c.samples >= min_samples
    ]
    if not filtered:
        return f"No data for {direction} heatmap."

    entry_buckets = sorted({c.entry_bucket for c in filtered})
    delta_buckets = sorted({c.btc_delta_bucket for c in filtered})

    lookup: dict[tuple[str, str], tuple[float, int]] = {}
    for c in filtered:
        key = (c.entry_bucket, c.btc_delta_bucket)
        prob = c.tp_probs.get(tp, 0.0)
        prev = lookup.get(key)
        if prev is None:
            lookup[key] = (prob, c.samples)
        else:
            p_prev, n_prev = prev
            n_tot = n_prev + c.samples
            wavg = (p_prev * n_prev + prob * c.samples) / n_tot
            lookup[key] = (wavg, n_tot)

    col_w = 8
    header = "entry\\delta".ljust(12) + "".join(d[:col_w].center(col_w) for d in delta_buckets)
    lines = [
        f"HEATMAP: Entry x BTC Delta -> TP{int(tp * 100)} ({direction})",
        header,
    ]
    for eb in entry_buckets:
        row = eb.ljust(12)
        for db in delta_buckets:
            val = lookup.get((eb, db))
            row += (f"{val[0]:.0%}" if val is not None else "—").center(col_w)
        lines.append(row)
    return "\n".join(lines)


def render_heatmap_time_delta(
    cells: list[EdgeCell],
    *,
    direction: str = "YES",
    tp: float = PRIMARY_EV_TP,
    min_samples: int = 10,
) -> str:
    """Seconds-left bucket (rows) x BTC delta bucket (cols) -> TP probability."""
    filtered = [
        c for c in cells
        if c.direction == direction and c.samples >= min_samples
    ]
    if not filtered:
        return f"No data for {direction} time heatmap."

    time_buckets = sorted({c.seconds_left_bucket for c in filtered}, key=_sort_time_bucket)
    delta_buckets = sorted({c.btc_delta_bucket for c in filtered})

    lookup: dict[tuple[str, str], tuple[float, int]] = {}
    for c in filtered:
        key = (c.seconds_left_bucket, c.btc_delta_bucket)
        prob = c.tp_probs.get(tp, 0.0)
        prev = lookup.get(key)
        if prev is None:
            lookup[key] = (prob, c.samples)
        else:
            p_prev, n_prev = prev
            n_tot = n_prev + c.samples
            wavg = (p_prev * n_prev + prob * c.samples) / n_tot
            lookup[key] = (wavg, n_tot)

    col_w = 8
    header = "time\\delta".ljust(10) + "".join(d[:col_w].center(col_w) for d in delta_buckets)
    lines = [
        f"HEATMAP: Seconds Left x BTC Delta -> TP{int(tp * 100)} ({direction})",
        header,
    ]
    for tb in time_buckets:
        row = tb.ljust(10)
        for db in delta_buckets:
            val = lookup.get((tb, db))
            row += (f"{val[0]:.0%}" if val is not None else "—").center(col_w)
        lines.append(row)
    return "\n".join(lines)


def _sort_time_bucket(label: str) -> int:
    if label.startswith(">="):
        return 999
    if label.startswith("<="):
        try:
            return int(label[2:])
        except ValueError:
            return 0
    return 0


def render_full_heatmap(cells: list[EdgeCell], *, min_samples: int = 10) -> str:
    parts = [
        render_heatmap_entry_delta(cells, direction="YES", min_samples=min_samples),
        "",
        render_heatmap_entry_delta(cells, direction="NO", min_samples=min_samples),
        "",
        render_heatmap_time_delta(cells, direction="YES", min_samples=min_samples),
        "",
        render_heatmap_time_delta(cells, direction="NO", min_samples=min_samples),
    ]
    return "\n".join(parts)
