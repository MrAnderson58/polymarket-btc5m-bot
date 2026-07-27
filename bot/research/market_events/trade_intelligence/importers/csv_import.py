"""CSV importer for Trade Intelligence V1."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from bot.research.market_events.trade_intelligence.knowledge import attach_default_layers
from bot.research.market_events.trade_intelligence.models import Outcome, TradeRecord
from bot.research.market_events.trade_intelligence.repository import TradeRepository

# Flexible header aliases
_ALIASES = {
    "symbol": ("symbol", "ticker", "asset"),
    "side": ("side", "direction"),
    "entry_ts": ("entry_ts", "entry_time", "timestamp", "ts"),
    "exit_ts": ("exit_ts", "exit_time"),
    "entry_price": ("entry_price", "entry", "entry_px"),
    "exit_price": ("exit_price", "exit", "exit_px"),
    "pnl_usd": ("pnl_usd", "pnl", "profit"),
    "pnl_pct": ("pnl_pct", "pnl_percent", "return_pct"),
    "strategy": ("strategy", "strategy_name"),
    "size": ("size", "notional", "qty"),
    "external_id": ("external_id", "id", "trade_id"),
}


def _pick(row: dict[str, str], key: str) -> str | None:
    for alias in _ALIASES.get(key, (key,)):
        for rk, rv in row.items():
            if rk.strip().lower() == alias:
                val = (rv or "").strip()
                return val if val != "" else None
    return None


def _f(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _i(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def import_csv_trades(conn: Any, path: str | Path) -> dict[str, int]:
    repo = TradeRepository(conn)
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"CSV not found: {p}")

    created = 0
    updated = 0
    with p.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for idx, row in enumerate(reader, start=1):
            symbol = _pick(row, "symbol")
            side = (_pick(row, "side") or "LONG").upper()
            if not symbol:
                continue
            external = _pick(row, "external_id") or f"csv:{p.name}:{idx}"
            existing = conn.execute(
                "SELECT id FROM ti_trades WHERE source = ? AND external_id = ?",
                ("csv", external),
            ).fetchone()
            pnl_usd = _f(_pick(row, "pnl_usd"))
            pnl_pct = _f(_pick(row, "pnl_pct"))
            exit_ts = _i(_pick(row, "exit_ts"))
            status = "closed" if exit_ts is not None else "open"
            rec = TradeRecord(
                id=None,
                source="csv",
                external_id=external,
                symbol=symbol.upper(),
                side=side,
                entry_ts=_i(_pick(row, "entry_ts")),
                exit_ts=exit_ts,
                entry_price=_f(_pick(row, "entry_price")),
                exit_price=_f(_pick(row, "exit_price")),
                size=_f(_pick(row, "size")),
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                strategy=_pick(row, "strategy"),
                status=status,
                raw_json=dict(row),
            )
            knowledge = attach_default_layers(repo, rec, tags=["csv"])
            tid = knowledge.trade.id
            assert tid is not None
            if status == "closed":
                result = "breakeven"
                if pnl_usd is not None:
                    if pnl_usd > 1e-9:
                        result = "win"
                    elif pnl_usd < -1e-9:
                        result = "loss"
                repo.upsert_outcome(
                    Outcome(
                        trade_id=tid,
                        outcome_ts=exit_ts,
                        result=result,
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                    ),
                )
            if existing:
                updated += 1
            else:
                created += 1
    return {"created": created, "updated": updated, "total": created + updated}
