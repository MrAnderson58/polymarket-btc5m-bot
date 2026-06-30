"""Fill audit: compare submitted CLOB orders with actual Polymarket executions."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bot.clob_client import get_authenticated_clob_client, wallet_configured
from bot.config import is_live_trading_enabled
from bot.database import (
    connect,
    fetch_submitted_orders_pending_fill_audit,
    get_order_fill_audit,
    init_db,
    update_order_fill_audit,
    upsert_order_fill_submitted,
)

logger = logging.getLogger(__name__)

CLOB_SHARES_SCALE = 1_000_000
FILL_EPSILON = 1e-6


@dataclass(frozen=True)
class SubmittedOrderSnapshot:
    idempotency_key: str
    clob_order_id: str
    clob_side: str
    token_id: str
    submitted_price: float
    submitted_shares: float
    submitted_notional: float


@dataclass(frozen=True)
class FillPending:
    matched_shares: float
    order_status: str
    raw_order: dict
    raw_trades: list[dict]


@dataclass(frozen=True)
class ResolvedFill:
    fill_shares: float
    average_fill_price: float
    fill_notional: float
    fees: float
    fill_status: str
    raw_order: dict
    raw_trades: list[dict]


FillResolution = FillPending | ResolvedFill


def _clob_side_from_idempotency_key(idempotency_key: str) -> str:
    if idempotency_key.endswith(":exit"):
        return "SELL"
    return "BUY"


def _parse_share_amount(raw: object) -> float:
    """Parse CLOB share amounts (micro-units or human-readable decimals)."""
    if raw in (None, ""):
        return 0.0
    text = str(raw).strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        return 0.0
    if "." in text:
        if value >= CLOB_SHARES_SCALE and value == value.to_integral_value():
            return float(value / CLOB_SHARES_SCALE)
        return float(value)
    return float(value / CLOB_SHARES_SCALE)


def _parse_price(raw: object) -> float:
    if raw in (None, ""):
        return 0.0
    try:
        return float(Decimal(str(raw).strip()))
    except InvalidOperation:
        return 0.0


def _trade_size_shares(trade: dict) -> float:
    for key in ("size", "matched_amount", "matchedAmount"):
        if key in trade and trade[key] not in (None, ""):
            return _parse_share_amount(trade[key])
    return 0.0


def _trade_price(trade: dict) -> float:
    for key in ("price", "match_price", "matchPrice"):
        if key in trade and trade[key] not in (None, ""):
            return _parse_price(trade[key])
    return 0.0


def _trade_fee_usdc(fee_source: dict, *, shares: float, price: float) -> float:
    fee_bps = fee_source.get("fee_rate_bps") or fee_source.get("feeRateBps") or 0
    try:
        bps = int(fee_bps)
    except (TypeError, ValueError):
        bps = 0
    if bps <= 0 or shares <= 0 or price <= 0:
        return 0.0
    notional = shares * price
    return notional * bps / 10_000


def _order_id_from(order: dict) -> str:
    return str(order.get("id") or order.get("orderID") or "")


def _extract_order_fill_from_trade(
    trade: dict,
    order_id: str,
) -> tuple[float, float, dict | None]:
    """Return shares, price, and fee source for this order within a trade."""
    if not order_id:
        return 0.0, 0.0, None

    taker_id = str(trade.get("taker_order_id") or trade.get("takerOrderId") or "")
    if taker_id == order_id:
        shares = _trade_size_shares(trade)
        price = _trade_price(trade)
        if shares > 0 and price > 0:
            return shares, price, trade
        return 0.0, 0.0, None

    for maker in trade.get("maker_orders") or trade.get("makerOrders") or []:
        if not isinstance(maker, dict):
            continue
        maker_id = str(maker.get("order_id") or maker.get("orderId") or "")
        if maker_id != order_id:
            continue
        shares = _parse_share_amount(maker.get("matched_amount") or maker.get("matchedAmount"))
        price = _parse_price(maker.get("price"))
        if shares > 0 and price > 0:
            return shares, price, maker
    return 0.0, 0.0, None


class ClobFillAdapter:
    """Fetch Polymarket order status and associated trades by order_id."""

    def __init__(self, client) -> None:
        self._client = client

    def fetch_order(self, order_id: str) -> dict | None:
        try:
            response = self._client.get_order(order_id)
        except Exception as exc:
            logger.warning("Fill audit: get_order failed for %s: %s", order_id, exc)
            return None
        if isinstance(response, dict):
            return response
        return None

    def fetch_trade(self, trade_id: str) -> dict | None:
        try:
            from py_clob_client_v2.clob_types import TradeParams

            trades = self._client.get_trades(TradeParams(id=trade_id), only_first_page=True)
        except Exception as exc:
            logger.debug("Fill audit: get_trades(id=%s) failed: %s", trade_id, exc)
            return None
        if not trades:
            return None
        trade = trades[0]
        return trade if isinstance(trade, dict) else None

    def fetch_trades_for_order(self, order: dict) -> list[dict]:
        trades: list[dict] = []
        seen: set[str] = set()
        order_id = _order_id_from(order)

        associate = order.get("associate_trades") or order.get("associateTrades") or []
        if isinstance(associate, list):
            for trade_id in associate:
                if not trade_id or trade_id in seen:
                    continue
                trade = self.fetch_trade(str(trade_id))
                if trade is not None:
                    seen.add(str(trade_id))
                    trades.append(trade)

        if trades or not order_id:
            return trades

        try:
            from py_clob_client_v2.clob_types import TradeParams

            for params in (
                TradeParams(id=order_id),
                TradeParams(asset_id=order.get("asset_id") or order.get("assetId")),
            ):
                if params.asset_id is None and params.id is None:
                    continue
                batch = self._client.get_trades(params, only_first_page=True)
                for trade in batch or []:
                    if not isinstance(trade, dict):
                        continue
                    trade_id = str(trade.get("id") or "")
                    if trade_id and trade_id in seen:
                        continue
                    taker_id = str(trade.get("taker_order_id") or trade.get("takerOrderId") or "")
                    if taker_id and taker_id != order_id:
                        maker_orders = trade.get("maker_orders") or trade.get("makerOrders") or []
                        maker_ids = {
                            str(item.get("order_id") or item.get("orderId") or "")
                            for item in maker_orders
                            if isinstance(item, dict)
                        }
                        if order_id not in maker_ids and taker_id != order_id:
                            continue
                    if trade_id:
                        seen.add(trade_id)
                    trades.append(trade)
                if trades:
                    break
        except Exception as exc:
            logger.debug("Fill audit: trade lookup fallback failed for %s: %s", order_id, exc)

        return trades


def resolve_fill(order: dict, trades: list[dict]) -> FillResolution:
    order_id = _order_id_from(order)
    original_shares = _parse_share_amount(order.get("original_size") or order.get("originalSize"))
    matched_shares = _parse_share_amount(order.get("size_matched") or order.get("sizeMatched"))
    order_status = str(order.get("status") or "UNKNOWN")

    legs: list[tuple[float, float, float]] = []
    for trade in trades:
        shares, price, fee_source = _extract_order_fill_from_trade(trade, order_id)
        if shares <= 0 or price <= 0 or fee_source is None:
            continue
        legs.append((shares, price, _trade_fee_usdc(fee_source, shares=shares, price=price)))

    if not legs:
        return FillPending(
            matched_shares=matched_shares,
            order_status=order_status,
            raw_order=order,
            raw_trades=trades,
        )

    fill_shares = sum(shares for shares, _, _ in legs)
    if fill_shares <= FILL_EPSILON:
        return FillPending(
            matched_shares=matched_shares,
            order_status=order_status,
            raw_order=order,
            raw_trades=trades,
        )

    weighted_price = sum(shares * price for shares, price, _ in legs)
    average_fill_price = weighted_price / fill_shares
    fees = sum(fee for _, _, fee in legs)
    fill_notional = fill_shares * average_fill_price

    if original_shares > FILL_EPSILON and fill_shares + FILL_EPSILON < original_shares:
        fill_status = "partial"
    else:
        fill_status = "filled"

    return ResolvedFill(
        fill_shares=fill_shares,
        average_fill_price=average_fill_price,
        fill_notional=fill_notional,
        fees=fees,
        fill_status=fill_status,
        raw_order=order,
        raw_trades=trades,
    )


def _compute_slippage(
    *,
    clob_side: str,
    submitted_price: float,
    fill_price: float,
) -> tuple[float, float]:
    price_difference = fill_price - submitted_price
    if submitted_price <= 0:
        return price_difference, 0.0
    if clob_side == "BUY":
        slippage = price_difference / submitted_price * 100
    else:
        slippage = (submitted_price - fill_price) / submitted_price * 100
    return price_difference, slippage


def log_fill_pending(
    *,
    submitted: SubmittedOrderSnapshot,
    matched_shares: float,
    order_status: str,
) -> None:
    logger.info(
        "FILL PENDING\n"
        "order_id=%s\n"
        "idempotency_key=%s\n"
        "submitted_price=%.6f\n"
        "matched_size=%.6f\n"
        "status=%s",
        submitted.clob_order_id,
        submitted.idempotency_key,
        submitted.submitted_price,
        matched_shares,
        order_status,
    )


def log_fill_audit(
    *,
    submitted: SubmittedOrderSnapshot,
    fill_shares: float,
    average_fill_price: float,
    fill_notional: float,
    fees: float,
    price_difference: float,
    slippage: float,
) -> None:
    logger.info(
        "FILL AUDIT\n"
        "order_id=%s\n"
        "idempotency_key=%s\n"
        "submitted_price=%.6f\n"
        "fill_price=%.6f\n"
        "price_difference=%+.6f\n"
        "submitted_shares=%.4f\n"
        "filled_shares=%.4f\n"
        "submitted_notional=%.6f\n"
        "filled_notional=%.6f\n"
        "fees=%.6f\n"
        "slippage=%+.4f%%",
        submitted.clob_order_id,
        submitted.idempotency_key,
        submitted.submitted_price,
        average_fill_price,
        price_difference,
        submitted.submitted_shares,
        fill_shares,
        submitted.submitted_notional,
        fill_notional,
        fees,
        slippage,
    )


def _submitted_from_intent(row: sqlite3.Row) -> SubmittedOrderSnapshot:
    submitted_price = float(row["price"])
    submitted_shares = float(row["shares"])
    return SubmittedOrderSnapshot(
        idempotency_key=row["idempotency_key"],
        clob_order_id=str(row["clob_order_id"]),
        clob_side=_clob_side_from_idempotency_key(row["idempotency_key"]),
        token_id=row["token_id"],
        submitted_price=submitted_price,
        submitted_shares=submitted_shares,
        submitted_notional=submitted_price * submitted_shares,
    )


def ensure_submitted_snapshot(conn: sqlite3.Connection, intent: sqlite3.Row) -> SubmittedOrderSnapshot:
    submitted = _submitted_from_intent(intent)
    upsert_order_fill_submitted(
        conn,
        idempotency_key=submitted.idempotency_key,
        clob_order_id=submitted.clob_order_id,
        clob_side=submitted.clob_side,
        token_id=submitted.token_id,
        submitted_price=submitted.submitted_price,
        submitted_shares=submitted.submitted_shares,
        submitted_notional=submitted.submitted_notional,
    )
    return submitted


def _should_log_pending(*, existing: sqlite3.Row | None, matched_shares: float) -> bool:
    if existing is None or existing["raw_order_json"] is None:
        return True
    last_logged = float(existing["last_logged_fill_shares"] or 0)
    return matched_shares > last_logged + FILL_EPSILON


def sync_order_fill(
    conn: sqlite3.Connection,
    intent: sqlite3.Row,
    *,
    adapter: ClobFillAdapter,
) -> bool:
    submitted = ensure_submitted_snapshot(conn, intent)
    existing = get_order_fill_audit(conn, submitted.idempotency_key)
    last_logged = 0.0 if existing is None else float(existing["last_logged_fill_shares"] or 0)

    order = adapter.fetch_order(submitted.clob_order_id)
    if order is None:
        return False

    trades = adapter.fetch_trades_for_order(order)
    resolution = resolve_fill(order, trades)

    if isinstance(resolution, FillPending):
        should_log = _should_log_pending(
            existing=existing,
            matched_shares=resolution.matched_shares,
        )
        update_order_fill_audit(
            conn,
            submitted.idempotency_key,
            fill_price=None,
            fill_shares=None,
            fill_notional=None,
            average_fill_price=None,
            fees=None,
            price_difference=None,
            slippage=None,
            fill_status="pending",
            last_logged_fill_shares=resolution.matched_shares if should_log else None,
            raw_order_json=json.dumps(resolution.raw_order, separators=(",", ":"), default=str),
            raw_trades_json=json.dumps(resolution.raw_trades, separators=(",", ":"), default=str),
        )
        if should_log:
            log_fill_pending(
                submitted=submitted,
                matched_shares=resolution.matched_shares,
                order_status=resolution.order_status,
            )
        return should_log

    price_difference, slippage = _compute_slippage(
        clob_side=submitted.clob_side,
        submitted_price=submitted.submitted_price,
        fill_price=resolution.average_fill_price,
    )
    should_log = resolution.fill_shares > last_logged + FILL_EPSILON
    update_order_fill_audit(
        conn,
        submitted.idempotency_key,
        fill_price=resolution.average_fill_price,
        fill_shares=resolution.fill_shares,
        fill_notional=resolution.fill_notional,
        average_fill_price=resolution.average_fill_price,
        fees=resolution.fees,
        price_difference=price_difference,
        slippage=slippage,
        fill_status=resolution.fill_status,
        last_logged_fill_shares=resolution.fill_shares if should_log else None,
        raw_order_json=json.dumps(resolution.raw_order, separators=(",", ":"), default=str),
        raw_trades_json=json.dumps(resolution.raw_trades, separators=(",", ":"), default=str),
    )
    if should_log:
        log_fill_audit(
            submitted=submitted,
            fill_shares=resolution.fill_shares,
            average_fill_price=resolution.average_fill_price,
            fill_notional=resolution.fill_notional,
            fees=resolution.fees,
            price_difference=price_difference,
            slippage=slippage,
        )
    return should_log


def sync_pending_order_fills(conn: sqlite3.Connection) -> int:
    """Sync submitted live orders with CLOB fill data. Returns count of new audit logs."""
    if not wallet_configured() or not is_live_trading_enabled():
        return 0

    try:
        adapter = ClobFillAdapter(get_authenticated_clob_client())
    except Exception as exc:
        logger.warning("Fill audit: CLOB client unavailable: %s", exc)
        return 0

    logged = 0
    for intent in fetch_submitted_orders_pending_fill_audit(conn):
        if sync_order_fill(conn, intent, adapter=adapter):
            logged += 1
    return logged


def run_fill_audit_sync() -> int:
    init_db()
    with connect() as conn:
        logged = sync_pending_order_fills(conn)
        conn.commit()
    return logged


def main() -> None:
    logged = run_fill_audit_sync()
    print(f"Fill audit sync complete; logged={logged}")


if __name__ == "__main__":
    sys.exit(main() or 0)
