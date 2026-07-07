"""Chronological market splits for walk-forward validation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketSplit:
    train: list[str]
    validation: list[str]
    test: list[str]

    def all_slugs(self) -> set[str]:
        return set(self.train) | set(self.validation) | set(self.test)

    def validate_no_overlap(self) -> None:
        train_set, val_set, test_set = set(self.train), set(self.validation), set(self.test)
        if train_set & val_set or train_set & test_set or val_set & test_set:
            raise ValueError("split partitions overlap")
        if len(self.all_slugs()) != len(self.train) + len(self.validation) + len(self.test):
            raise ValueError("duplicate slugs across splits")


def market_start_ts(path: list[dict]) -> int:
    if path and path[0].get("window_start_ts") is not None:
        return int(path[0]["window_start_ts"])
    return int(path[0]["timestamp"]) if path else 0


def sort_markets_chronologically(paths: dict[str, list[dict]]) -> list[str]:
    return sorted(paths.keys(), key=lambda s: (market_start_ts(paths[s]), s))


def split_markets_chronological(
    paths: dict[str, list[dict]],
    *,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    test_ratio: float = 0.20,
) -> MarketSplit:
    total = train_ratio + validation_ratio + test_ratio
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"ratios must sum to 1.0, got {total}")

    ordered = sort_markets_chronologically(paths)
    n = len(ordered)
    if n == 0:
        return MarketSplit(train=[], validation=[], test=[])

    n_train = max(1, int(n * train_ratio))
    n_val = max(0, int(n * validation_ratio))
    n_test = n - n_train - n_val
    if n_test < 0:
        n_test = 0
        n_val = n - n_train

    split = MarketSplit(
        train=ordered[:n_train],
        validation=ordered[n_train:n_train + n_val],
        test=ordered[n_train + n_val:],
    )
    split.validate_no_overlap()
    return split
