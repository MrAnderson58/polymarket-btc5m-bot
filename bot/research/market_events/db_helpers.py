"""Backend-neutral SQLite row/scalar helpers."""

from __future__ import annotations

from typing import Any


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        pass
    if hasattr(row, "keys"):
        keys = list(row.keys())
        if len(keys) == 1:
            return row[keys[0]]
        for k in keys:
            if k.lower() == key.lower():
                return row[k]
    if isinstance(row, (tuple, list)) and row:
        return row[0]
    return default


def scalar(row: Any, default: int | float = 0) -> int | float:
    val = row_get(row, "n", default)
    if val is None:
        val = row_get(row, "count", default)
    if val is None and row is not None and hasattr(row, "keys"):
        keys = list(row.keys())
        if keys:
            val = row[keys[0]]
    return default if val is None else val
