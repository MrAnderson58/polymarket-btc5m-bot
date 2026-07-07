"""Chronological split audit."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.research.strategy_simulator.splits import MarketSplit, market_start_ts


@dataclass
class SplitAudit:
    split_name: str
    min_market_ts: int | None = None
    max_market_ts: int | None = None
    duplicate_slug_count: int = 0
    ordering_violations: int = 0
    overlap_with_train: int = 0
    overlap_with_validation: int = 0
    overlap_with_test: int = 0
    assertions: list[str] = field(default_factory=list)
    ok: bool = True

    def add_assertion(self, msg: str, *, passed: bool) -> None:
        status = "PASS" if passed else "FAIL"
        self.assertions.append(f"{status}: {msg}")
        if not passed:
            self.ok = False


def _ts_range(slugs: list[str], paths: dict[str, list[dict]]) -> tuple[int | None, int | None]:
    if not slugs:
        return None, None
    starts = [market_start_ts(paths[s]) for s in slugs if s in paths]
    if not starts:
        return None, None
    return min(starts), max(starts)


def audit_split(
    split: MarketSplit,
    paths: dict[str, list[dict]],
) -> dict[str, SplitAudit]:
    audits: dict[str, SplitAudit] = {}
    partitions = {
        "TRAIN": split.train,
        "VALIDATION": split.validation,
        "TEST": split.test,
    }

    for name, slugs in partitions.items():
        a = SplitAudit(split_name=name)
        if slugs:
            a.min_market_ts, a.max_market_ts = _ts_range(slugs, paths)
            a.duplicate_slug_count = len(slugs) - len(set(slugs))
            starts = [market_start_ts(paths[s]) for s in slugs if s in paths]
            for i in range(1, len(starts)):
                if starts[i] < starts[i - 1]:
                    a.ordering_violations += 1
        audits[name] = a

    train_set = set(split.train)
    val_set = set(split.validation)
    test_set = set(split.test)

    audits["VALIDATION"].overlap_with_train = len(train_set & val_set)
    audits["TEST"].overlap_with_train = len(train_set & test_set)
    audits["TEST"].overlap_with_validation = len(val_set & test_set)

    train_max = audits["TRAIN"].max_market_ts
    val_min = audits["VALIDATION"].min_market_ts
    val_max = audits["VALIDATION"].max_market_ts
    test_min = audits["TEST"].min_market_ts

    global_audit = SplitAudit(split_name="GLOBAL")
    if train_max is not None and val_min is not None:
        global_audit.add_assertion(
            f"train_max ({train_max}) < validation_min ({val_min})",
            passed=train_max < val_min,
        )
    if val_max is not None and test_min is not None:
        global_audit.add_assertion(
            f"validation_max ({val_max}) < test_min ({test_min})",
            passed=val_max < test_min,
        )
    global_audit.add_assertion(
        "no overlapping market_slug across splits",
        passed=not (train_set & val_set or train_set & test_set or val_set & test_set),
    )
    global_audit.add_assertion(
        "no duplicate slugs within splits",
        passed=all(a.duplicate_slug_count == 0 for a in audits.values()),
    )
    audits["GLOBAL"] = global_audit
    return audits
