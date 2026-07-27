"""Telegram signal importer — stub for future wiring (no live Telegram ingest in V1)."""

from __future__ import annotations

from typing import Any


def import_telegram_stub(conn: Any, **_kwargs: Any) -> dict[str, Any]:
    """Future: map Telegram signal → ti_trades + ti_telegram.

    V1 only reserves the source name and documents the plug-in point.
    """
    _ = conn
    return {
        "created": 0,
        "updated": 0,
        "total": 0,
        "status": "not_implemented",
        "message": "Telegram signal import is reserved for a future stage",
    }
