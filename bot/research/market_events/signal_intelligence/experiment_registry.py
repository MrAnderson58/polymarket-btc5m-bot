"""Experiment Registry V1 — strategy ideas for offline side-by-side replay.

Read-only catalog. Registration never mutates production strategy or paper book.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_REJECTED = "rejected"

VALID_STATUSES = frozenset({STATUS_DRAFT, STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_REJECTED})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Experiment:
    """One strategy idea evaluated offline against historical trades."""

    id: str
    name: str
    description: str
    author: str
    created_at: str
    version: str
    status: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def filter_params_dict(self) -> dict[str, Any]:
        """Subset of parameters used by FilterParams replay."""
        p = self.parameters or {}
        return {
            "disabled_symbols": list(p.get("disabled_symbols") or []),
            "confidence_threshold": p.get("confidence_threshold"),
            "label": self.id,
        }


class ExperimentRegistry:
    """In-memory registry. Process-local; never writes to production state."""

    def __init__(self) -> None:
        self._by_id: dict[str, Experiment] = {}

    def register(self, experiment: Experiment, *, replace: bool = False) -> Experiment:
        eid = str(experiment.id).strip()
        if not eid:
            raise ValueError("experiment.id is required")
        if experiment.status not in VALID_STATUSES:
            raise ValueError(f"invalid status {experiment.status!r}")
        if eid in self._by_id and not replace:
            raise ValueError(f"experiment already registered: {eid}")
        exp = Experiment(
            id=eid,
            name=str(experiment.name),
            description=str(experiment.description),
            author=str(experiment.author),
            created_at=str(experiment.created_at or _utc_now_iso()),
            version=str(experiment.version or "1"),
            status=str(experiment.status),
            parameters=copy.deepcopy(experiment.parameters or {}),
        )
        self._by_id[eid] = exp
        return exp

    def get(self, experiment_id: str) -> Experiment | None:
        return self._by_id.get(str(experiment_id))

    def list(self, *, status: str | None = None) -> list[Experiment]:
        items = list(self._by_id.values())
        if status is not None:
            items = [e for e in items if e.status == status]
        return sorted(items, key=lambda e: e.id)

    def unregister(self, experiment_id: str) -> bool:
        return self._by_id.pop(str(experiment_id), None) is not None

    def clear(self) -> None:
        self._by_id.clear()

    def __len__(self) -> int:
        return len(self._by_id)


def make_experiment(
    *,
    id: str,
    name: str,
    description: str,
    author: str = "research",
    version: str = "1",
    status: str = STATUS_ACTIVE,
    parameters: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> Experiment:
    return Experiment(
        id=id,
        name=name,
        description=description,
        author=author,
        created_at=created_at or _utc_now_iso(),
        version=version,
        status=status,
        parameters=dict(parameters or {}),
    )


def default_experiments() -> list[Experiment]:
    """Built-in strategy ideas (V1). Safe offline filters only."""
    stamp = "2026-07-30T00:00:00+00:00"
    return [
        make_experiment(
            id="conf_gate_055",
            name="Confidence gate 0.55",
            description="Skip entries with confidence below 0.55 (fail-open if missing).",
            author="experiment-framework",
            version="1",
            created_at=stamp,
            parameters={"confidence_threshold": 0.55, "disabled_symbols": []},
        ),
        make_experiment(
            id="conf_gate_065",
            name="Confidence gate 0.65",
            description="Skip entries with confidence below 0.65.",
            author="experiment-framework",
            version="1",
            created_at=stamp,
            parameters={"confidence_threshold": 0.65, "disabled_symbols": []},
        ),
        make_experiment(
            id="conf_gate_075",
            name="Confidence gate 0.75",
            description="Skip entries with confidence below 0.75.",
            author="experiment-framework",
            version="1",
            created_at=stamp,
            parameters={"confidence_threshold": 0.75, "disabled_symbols": []},
        ),
        make_experiment(
            id="disable_eth",
            name="Disable ETH symbol",
            description="Replay book as if ETH were disabled at entry.",
            author="experiment-framework",
            version="1",
            created_at=stamp,
            parameters={"confidence_threshold": None, "disabled_symbols": ["ETH"]},
        ),
        make_experiment(
            id="disable_sol",
            name="Disable SOL symbol",
            description="Replay book as if SOL were disabled at entry.",
            author="experiment-framework",
            version="1",
            created_at=stamp,
            parameters={"confidence_threshold": None, "disabled_symbols": ["SOL"]},
        ),
        make_experiment(
            id="bundle_conf065_no_eth",
            name="Bundle: conf≥0.65 + no ETH",
            description="Combined confidence and symbol filter vs production baseline.",
            author="experiment-framework",
            version="1",
            created_at=stamp,
            parameters={"confidence_threshold": 0.65, "disabled_symbols": ["ETH"]},
        ),
    ]


def build_default_registry() -> ExperimentRegistry:
    reg = ExperimentRegistry()
    for exp in default_experiments():
        reg.register(exp)
    return reg


__all__ = [
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "STATUS_DRAFT",
    "STATUS_REJECTED",
    "VALID_STATUSES",
    "Experiment",
    "ExperimentRegistry",
    "build_default_registry",
    "default_experiments",
    "make_experiment",
]
