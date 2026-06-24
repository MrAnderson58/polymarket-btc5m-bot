"""Debug locally signed CLOB V2 order structure (create_order only, no POST /order)."""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import sys
from dataclasses import asdict
from typing import Any

import py_clob_client_v2
from py_clob_client_v2 import OrderArgs, OrderType
from py_clob_client_v2.order_builder.constants import BUY
from py_clob_client_v2.order_utils.model.order_data_v2 import order_to_json_v2

from bot.clob_client import get_authenticated_clob_client, wallet_configured
from bot.config import (
    CHAIN_ID,
    CLOB_HOST,
    EARLY_REVERSION_POSITION_SIZE_USDC,
    POLY_PROXY_WALLET,
    POLY_SIGNATURE_TYPE,
)
from bot.market_scanner import find_active_btc_5m_market, get_best_bid_ask

VERSION_FIELDS = ("version", "orderVersion", "order_version", "signatureType", "signature_type")

PACKAGES = (
    ("py_clob_client_v2", "py-clob-client-v2"),
    ("py_order_utils", "py-order-utils"),
    ("py_builder_signing_sdk", "py-builder-signing-sdk"),
    ("poly_eip712_structs", "poly-eip712-structs"),
)

V2_ORDER_FIELDS = ("timestamp", "metadata", "builder")


def _section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _signed_order_dict(order: Any, owner: str = "") -> dict[str, Any]:
    if hasattr(order, "dict") and callable(order.dict):
        return order.dict()
    payload = order_to_json_v2(order, owner, OrderType.GTC)
    return payload["order"]


def _print_versions() -> None:
    _section("Package location and versions")
    print(f"py_clob_client_v2 location: {inspect.getfile(py_clob_client_v2)}")
    for module_name, distribution in PACKAGES:
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = "NOT INSTALLED"
        print(f"{module_name:22} {version}")


def _collect_version_like_fields(obj: Any, prefix: str = "") -> dict[str, Any]:
    found: dict[str, Any] = {}
    names = [name for name in dir(obj) if not name.startswith("_")]
    for name in names:
        key = f"{prefix}.{name}" if prefix else name
        lower = name.lower()
        if any(token in lower for token in ("version", "signaturetype", "signature_type")):
            try:
                found[key] = getattr(obj, name)
            except Exception as exc:
                found[key] = f"<error: {exc}>"
    return found


def main() -> int:
    _print_versions()

    _section("Runtime config")
    print(f"CHAIN_ID:            {CHAIN_ID}")
    print(f"POLY_SIGNATURE_TYPE: {POLY_SIGNATURE_TYPE}")
    print(f"POLY_PROXY_WALLET:   {POLY_PROXY_WALLET}")

    if not wallet_configured():
        print("\nPOLY_PRIVATE_KEY is not set")
        return 1

    market = find_active_btc_5m_market()
    if market is None:
        print("\nNo active BTC 5m market found")
        return 1

    quotes = get_best_bid_ask(market)
    token_id = None
    price = None
    for side_quotes in (market.yes_quotes, market.no_quotes):
        if side_quotes.ask and side_quotes.ask > 0:
            token_id = side_quotes.token_id
            price = side_quotes.ask
            break
    if token_id is None or price is None:
        print("\nNo valid ask on active BTC 5m market")
        return 1

    shares = EARLY_REVERSION_POSITION_SIZE_USDC / price
    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=shares,
        side=BUY,
    )

    _section("Market and OrderArgs")
    print(f"market.slug:     {market.slug}")
    print(f"window_start_ts: {market.window_start_ts}")
    print(f"token_id:        {token_id}")
    print(f"quotes:          {_pretty(quotes)}")
    print(f"order_args:      {order_args}")

    client = get_authenticated_clob_client(force_new=True)
    order = client.create_order(order_args)

    _section("Signed order (create_order only)")
    print(f"type(client): {type(client)!r}")
    print(f"type(order): {type(order)!r}")

    print("\norder (repr):")
    print(order)

    order_dict = _signed_order_dict(order, client.creds.api_key if client.creds else "")
    print("\norder.dict() full JSON:")
    print(json.dumps(order_dict, indent=2, sort_keys=True))

    _section("V2 order field presence")
    for field in V2_ORDER_FIELDS:
        present = field in order_dict
        print(f"{field}: {'YES' if present else 'NO'} = {order_dict.get(field)!r}")

    post_body = order_to_json_v2(order, client.creds.api_key, OrderType.GTC, post_only=False)
    _section("POST /order request body preview (NOT sent)")
    print(f"endpoint: POST {CLOB_HOST}/order")
    print(json.dumps(post_body, indent=2, sort_keys=True))

    print("\ndataclass asdict(order):")
    print(_pretty(asdict(order)))

    _section("Version / signatureType-like fields")
    hits: dict[str, Any] = {}
    hits.update(_collect_version_like_fields(order))
    if hits:
        print(_pretty(hits))
    else:
        print("No version / orderVersion / signatureType-like fields found on signed order object.")

    for field in VERSION_FIELDS:
        if hasattr(order, field):
            print(f"order.{field} = {getattr(order, field)!r}")

    print("\nPOST /order was NOT called.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
