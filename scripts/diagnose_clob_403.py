#!/usr/bin/env python3
"""
Diagnose Polymarket CLOB 403 "Trading restricted in your region".

Does not place a real order unless --submit is passed. By default it signs
locally and POSTs to /order with a tiny GTC limit order (same path as live bot).

Usage:
  python scripts/diagnose_clob_403.py
  python scripts/diagnose_clob_403.py --submit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import httpx
from dotenv import load_dotenv
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams, OrderArgs
from py_clob_client.exceptions import PolyApiException
from py_clob_client.http_helpers import helpers as http_helpers
from py_clob_client.order_builder.constants import BUY

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from bot.clob_client import get_authenticated_clob_client  # noqa: E402
from bot.config import (  # noqa: E402
    CHAIN_ID,
    CLOB_HOST,
    EARLY_REVERSION_POSITION_SIZE_USDC,
    POLY_PRIVATE_KEY,
    POLY_PROXY_WALLET,
    POLY_SIGNATURE_TYPE,
)
from bot.market_scanner import find_active_btc_5m_market  # noqa: E402


def _section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _print_response(label: str, resp: httpx.Response) -> None:
    print(f"\n--- {label} ---")
    print(f"URL:     {resp.request.method} {resp.request.url}")
    print(f"Status:  {resp.status_code}")
    print("Headers:")
    for key in ("server", "cf-ray", "cf-mitigated", "x-cache", "content-type"):
        if key in resp.headers:
            print(f"  {key}: {resp.headers[key]}")
    body = resp.text
    try:
        parsed = resp.json()
        print("Body (json):")
        print(json.dumps(parsed, indent=2))
    except Exception:
        print("Body (text):")
        print(body[:2000])


def _probe_ip() -> None:
    _section("1. Egress IP / coarse geolocation (same machine as bot)")
    for url in (
        "https://api.ipify.org?format=json",
        "https://ipapi.co/json/",
    ):
        try:
            r = httpx.get(url, timeout=10)
            print(f"{url} -> {r.status_code}")
            print(r.text[:500])
        except Exception as exc:
            print(f"{url} -> error: {exc}")


def _probe_py_clob_client_http_layer() -> None:
    _section("2. py-clob-client HTTP layer capabilities")
    client = http_helpers._http_client
    print(f"Global HTTP client type: {type(client)}")
    print(f"HTTP/2 enabled: {getattr(client, '_http2', 'unknown')}")
    print("Proxy support: NOT exposed by py-clob-client (global httpx.Client, no proxy arg)")
    print("Custom session: NOT exposed (module-level _http_client in http_helpers.helpers)")
    print("Custom transport: NOT exposed")
    print("Custom headers: only via overloadHeaders() — User-Agent, Accept, Content-Type")
    print(f"CLOB host from config: {CLOB_HOST}")
    print("create_and_post_order() flow:")
    print("  create_order()  -> local sign + GET /tick-size, /neg-risk, /fee-rate on CLOB host")
    print("  post_order()    -> POST {CLOB_HOST}/order  (authenticated L2)")


def _probe_endpoints(client) -> dict[str, dict]:
    _section("3. Endpoint probes (auth vs read vs write)")
    signer = client.get_address()
    results: dict[str, dict] = {}

    probes: list[tuple[str, str, str, dict | None]] = [
        ("public", "GET", f"{CLOB_HOST}/time", None),
        ("l2 balance", "GET", f"{CLOB_HOST}/balance-allowance?asset_type=COLLATERAL&signature_type={POLY_SIGNATURE_TYPE}", "l2"),
        ("l2 open orders", "GET", f"{CLOB_HOST}/data/orders", "l2"),
        ("ban status", "GET", f"{CLOB_HOST}/auth/ban-status/closed-only", "l2"),
    ]

    market = find_active_btc_5m_market()
    token_id = None
    price = None
    if market:
        for quotes in (market.yes_quotes, market.no_quotes):
            if quotes.ask and quotes.ask > 0:
                token_id = quotes.token_id
                price = quotes.ask
                break
        probes.append(
            ("public price", "GET", f"{CLOB_HOST}/price?token_id={token_id}&side=BUY", None)
        )

    raw = httpx.Client(http2=True, timeout=30)
    try:
        for name, method, url, auth in probes:
            headers = {"User-Agent": "diagnose_clob_403", "Accept": "*/*"}
            if auth == "l2":
                from py_clob_client.headers.headers import create_level_2_headers
                from py_clob_client.clob_types import RequestArgs

                path = url.replace(CLOB_HOST, "")
                request_args = RequestArgs(method=method, request_path=path.split("?")[0])
                headers = create_level_2_headers(client.signer, client.creds, request_args)
            try:
                resp = raw.request(method, url, headers=headers)
                results[name] = {"url": url, "status": resp.status_code, "body": resp.text[:500]}
                _print_response(name, resp)
            except Exception as exc:
                results[name] = {"url": url, "error": str(exc)}
                print(f"{name}: error {exc}")
    finally:
        raw.close()

    print(f"\nSigner EOA:  {signer}")
    print(f"Funder/proxy: {POLY_PROXY_WALLET or signer}")
    print(f"signature_type={POLY_SIGNATURE_TYPE} chain_id={CHAIN_ID}")
    if market and token_id and price:
        print(f"Test market: {market.slug} token={token_id[:16]}... ask={price}")
    return results


def _submit_tiny_order(client, *, do_submit: bool) -> None:
    _section("4. Order path: create_order (local) -> POST /order")

    market = find_active_btc_5m_market()
    if not market:
        print("No active BTC 5m market — cannot build order")
        return

    token_id = None
    price = None
    for quotes in (market.yes_quotes, market.no_quotes):
        if quotes.ask and quotes.ask > 0:
            token_id = quotes.token_id
            price = quotes.ask
            break
    if not token_id or not price:
        print("No ask on active market")
        return

    shares = EARLY_REVERSION_POSITION_SIZE_USDC / price
    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=shares,
        side=BUY,
    )

    print(f"OrderArgs: token_id={token_id[:20]}... price={price} size={shares:.4f} side=BUY")
    print(f"POST target will be: {CLOB_HOST}/order")

    try:
        signed = client.create_order(order_args)
        print("create_order(): OK (local sign + metadata fetches succeeded)")
        print(f"Signed order type: {type(signed)}")
    except PolyApiException as exc:
        print(f"create_order() PolyApiException: {exc}")
        if exc.status_code:
            print("  -> failure during metadata fetch (tick-size/neg-risk/fee-rate), not POST /order")
        return
    except Exception as exc:
        print(f"create_order() failed: {exc}")
        traceback.print_exc()
        return

    if not do_submit:
        print("\nDry run: skipping POST /order (pass --submit to send)")
        return

    post_url = f"{CLOB_HOST}/order"
    print(f"\nSubmitting via client.post_order() -> {post_url}")

    try:
        response = client.post_order(signed)
        print("post_order() SUCCESS")
        print(json.dumps(response, indent=2) if isinstance(response, dict) else response)
    except PolyApiException as exc:
        print(f"post_order() PolyApiException: {exc}")
        print(f"Exact endpoint: POST {post_url}")
        print(f"status_code={exc.status_code}")
        print(f"error_message={exc.error_msg}")
    except Exception as exc:
        print(f"post_order() failed: {exc}")
        traceback.print_exc()


def _summarize(results: dict[str, dict]) -> None:
    _section("5. Diagnosis summary")
    balance = results.get("l2 balance", {})
    orders = results.get("l2 open orders", {})
    ban = results.get("ban status", {})

    print("Does create_and_post_order hit Polymarket directly?")
    print(f"  YES — httpx POST to {CLOB_HOST}/order (no intermediary in py-clob-client)")
    print()
    print("py-clob-client proxy/session customization:")
    print("  NO public API — fixed module-level httpx.Client(http2=True)")
    print()
    print("Likely 403 endpoint:")
    print(f"  POST {CLOB_HOST}/order")
    print()
    print("Restriction basis (from endpoint split):")

    bal_ok = balance.get("status") == 200
    ord_ok = orders.get("status") == 200
    print(f"  API credentials (L2 auth): {'PASS' if bal_ok and ord_ok else 'CHECK'}")
    print(f"    GET /balance-allowance -> {balance.get('status', balance.get('error', '?'))}")
    print(f"    GET /data/orders       -> {orders.get('status', orders.get('error', '?'))}")
    print(f"  Wallet address ban:      GET /auth/ban-status -> {ban.get('status', '?')}")
    print("  IP / country geolocation: if reads pass but POST /order returns 403")
    print("    'Trading restricted in your region' -> geoblock on order submission IP")
    print()
    print("Auth vs geoblock:")
    if bal_ok:
        print("  Authentication SUCCEEDS (balance/orders readable with same API key)")
        print("  Order POST is separately geoblocked — typical regional IP restriction")
    else:
        print("  Could not confirm L2 reads — check credentials separately")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Polymarket CLOB 403 geoblock")
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Actually POST /order (tiny GTC limit buy on active BTC 5m market)",
    )
    args = parser.parse_args()

    if not POLY_PRIVATE_KEY:
        print("POLY_PRIVATE_KEY not set in .env")
        return 1

    _probe_ip()
    _probe_py_clob_client_http_layer()

    client = get_authenticated_clob_client(force_new=True)
    # touch balance via SDK to confirm creds
    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        client.update_balance_allowance(params)
        bal = client.get_balance_allowance(params)
        print(f"\nSDK balance-allowance OK: balance={bal.get('balance')}")
    except PolyApiException as exc:
        print(f"\nSDK balance-allowance PolyApiException: {exc}")

    results = _probe_endpoints(client)
    _submit_tiny_order(client, do_submit=args.submit)
    _summarize(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
