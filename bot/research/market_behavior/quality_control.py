"""Quality control checks for market behavior and simulator outputs."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.research.market_behavior.config import EDGE_TP_LEVELS, MIN_EDGE_SAMPLES
from bot.research.market_behavior.edge_finder import EdgeCell
from bot.research.market_behavior.tp_probability import check_tp_monotonic


@dataclass
class QcReport:
    warnings: list[str] = field(default_factory=list)
    rejected_keys: set[tuple] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.warnings


def validate_edge_cells(cells: list[EdgeCell], *, min_samples: int | None = None) -> QcReport:
    """Validate edge statistics; mark cells that fail QC for exclusion from ranking."""
    report = QcReport()
    floor = min_samples or MIN_EDGE_SAMPLES

    for cell in cells:
        if cell.samples < floor:
            report.rejected_keys.add(cell.combo_key)
            continue

        for tp, prob in cell.tp_probs.items():
            if prob < 0 or prob > 1:
                report.warnings.append(
                    f"invalid probability {prob:.4f} for {cell.combo_key} TP{int(tp*100)}"
                )
                report.rejected_keys.add(cell.combo_key)

        for w in check_tp_monotonic(cell.tp_probs):
            report.warnings.append(f"{cell.combo_key}: {w}")
            report.rejected_keys.add(cell.combo_key)

        for tp in EDGE_TP_LEVELS:
            realized_ev = cell.ev_by_tp.get(tp)
            if realized_ev is None:
                continue
            exp_p = cell.expected_profit_by_tp.get(tp, 0.0)
            exp_l = cell.expected_loss_by_tp.get(tp, 0.0)
            formula_ev = exp_p - exp_l
            if abs(formula_ev - realized_ev) > 0.05 and cell.samples >= floor:
                report.warnings.append(
                    f"{cell.combo_key}: EV mismatch TP{int(tp*100)} "
                    f"formula={formula_ev:.4f} realized={realized_ev:.4f}"
                )

    return report


def filter_rankable_cells(cells: list[EdgeCell], qc: QcReport) -> list[EdgeCell]:
    return [c for c in cells if c.combo_key not in qc.rejected_keys]


def render_qc_warnings(qc: QcReport) -> str:
    if qc.ok:
        return "QC: OK"
    lines = ["QC WARNINGS", "-" * 20]
    lines.extend(f"  ! {w}" for w in qc.warnings[:20])
    if len(qc.warnings) > 20:
        lines.append(f"  ... and {len(qc.warnings) - 20} more")
    return "\n".join(lines)
