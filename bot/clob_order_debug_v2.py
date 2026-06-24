"""CLOB V2 signed order debug and optional dry submit (post_only, no immediate fill)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from py_clob_client_v2 import OrderArgs, OrderType
from py_clob_client_v2.exceptions import PolyApiException
from py_clob_client_v2.order_builder.constants import BUY
from py_clob_client_v2.order_utils.model.order_data_v2 import order_to_json_v2

from bot.clob_client import get_authenticated_clob_client, wallet_configured
from bot.config import EARLY_REVERSION_POSITION_SIZE_USDC
from bot.market_scanner import find_active_btc_5m_market, get_best_bid_ask

V2_ORDER_FIELDS = ("timestamp", "metadata", "builder")


def _section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _signed_order_dict(order: Any, owner: str = "") -> dict[str, Any]:
    if hasattr(order, "dict") and callable(order.dict):
        return order.dict()
    payload = order_to_json_v2(order, owner, OrderType.GTC)
    return payload["order"]


def _pick_market_order() -> tuple[Any, str, float, float | None]:
    market = find_active_btc_5m_market()
    if market is None:
        raise RuntimeError("No active BTC 5m market found")

    quotes = get_best_bid_ask(market)
    for side_quotes in (market.yes_quotes, market.no_quotes):
        if side_quotes.ask and side_quotes.ask > 0:
            return market, side_quotes.token_id, side_quotes.ask, side_quotes.bid

    raise RuntimeError("No valid ask on active BTC 5m market")


def _resting_buy_price(ask: float, bid: float | None) -> float:
    tick = 0.01
    candidate = ask - tick
    if bid is not None:
        candidate = min(candidate, bid - tick)
    return max(0.01, round(candidate, 2))


def _print_order_inspection(client: Any, order: Any) -> dict[str, Any]:
    _section("Client and signed order types")
    print(f"type(client): {type(client)!r}")
    print(f"type(order):  {type(order)!r}")

    owner = client.creds.api_key if client.creds else ""
    order_dict = _signed_order_dict(order, owner)

    _section("order.dict()")
    print(json.dumps(order_dict, indent=2, sort_keys=True))

    _section("V2 order field presence")
    for field in V2_ORDER_FIELDS:
        present = field in order_dict
        print(f"{field}: {'YES' if present else 'NO'} = {order_dict.get(field)!r}")

    return order_dict


def _dry_submit(client: Any, order: Any) -> None:
    _section("Dry submit (post_only=True, no immediate fill)")
    try:
        response = client.post_order(order, order_type=OrderType.GTC, post_only=True)
        print("Server response (full):")
        print(json.dumps(response, indent=2, sort_keys=True, default=str))
    except PolyApiException as exc:
        print(f"PolyApiException: {exc}")
        print(f"status_code: {exc.status_code}")
        print("error_message (full):")
        print(json.dumps(exc.error_msg, indent=2, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CLOB V2 order debug")
    parser.add_argument(
        "--submit",
        action="store_true",
        help="POST /order with post_only=True (resting order, no immediate fill)",
    )
    args = parser.parse_args(argv)

    if not wallet_configured():
        print("POLY_PRIVATE_KEY is not set")
        return 1

    try:
        market, token_id, ask_price, bid_price = _pick_market_order()
    except RuntimeError as exc:
        print(str(exc))
        return 1

    price = ask_price
    size_usdc = max(EARLY_REVERSION_POSITION_SIZE_USDC, 1.05)
    if args.submit:
        price = _resting_buy_price(ask_price, bid_price)

    shares = size_usdc / price
    if args.submit:
        shares = max(5.0, shares)
    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=shares,
        side=BUY,
    )

    _section("Market")
    print(f"slug:     {market.slug}")
    print(f"token_id: {token_id}")
    print(f"ask:      {ask_price}")
    print(f"bid:      {bid_price}")
    print(f"price:    {price}")
    print(f"quotes:   {json.dumps(get_best_bid_ask(market), indent=2)}")

    client = get_authenticated_clob_client(force_new=True)
    order = client.create_order(order_args)
    _print_order_inspection(client, order)

    if args.submit:
        _dry_submit(client, order)
    else:
        print("\nDry submit skipped. Re-run with --submit to POST /order (post_only).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
