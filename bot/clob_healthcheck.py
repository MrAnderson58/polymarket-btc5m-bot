"""Polymarket CLOB connectivity and trading readiness healthcheck."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from py_clob_client_v2 import AssetType, BalanceAllowanceParams, OrderArgs
from py_clob_client_v2.order_builder.constants import BUY

from bot.clob_client import get_authenticated_clob_client, wallet_configured
from bot.config import EARLY_REVERSION_POSITION_SIZE_USDC, POLY_PROXY_WALLET
from bot.market_scanner import find_active_btc_5m_market


USDC_DECIMALS = 1_000_000


@dataclass(frozen=True)
class HealthcheckResult:
    name: str
    passed: bool
    details: str = ""


def _status(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _to_usdc(raw: object) -> float:
    if raw in (None, ""):
        return 0.0
    return int(str(raw)) / USDC_DECIMALS


def _check_wallet() -> HealthcheckResult:
    if not wallet_configured():
        return HealthcheckResult("Wallet", False, "POLY_PRIVATE_KEY is not set")
    try:
        client = get_authenticated_clob_client(force_new=True)
        address = client.get_address()
        if not address:
            return HealthcheckResult("Wallet", False, "empty wallet address")
        funder = f", funder={POLY_PROXY_WALLET}" if POLY_PROXY_WALLET else ""
        return HealthcheckResult("Wallet", True, f"{address}{funder}")
    except Exception as exc:
        return HealthcheckResult("Wallet", False, str(exc))


def _check_api_creds() -> HealthcheckResult:
    if not wallet_configured():
        return HealthcheckResult("API creds", False, "POLY_PRIVATE_KEY is not set")
    try:
        client = get_authenticated_clob_client(force_new=True)
        creds = client.creds
        if creds is None or not creds.api_key or not creds.api_secret or not creds.api_passphrase:
            return HealthcheckResult("API creds", False, "missing api_key/secret/passphrase")
        return HealthcheckResult("API creds", True, f"api_key={creds.api_key[:8]}...")
    except Exception as exc:
        return HealthcheckResult("API creds", False, str(exc))


def _parse_allowance_usdc(response: dict) -> float:
    raw_allowance = response.get("allowance")
    if raw_allowance not in (None, ""):
        return _to_usdc(raw_allowance)

    allowances = response.get("allowances")
    if isinstance(allowances, dict) and allowances:
        return max(_to_usdc(value) for value in allowances.values())
    return 0.0


def _fetch_collateral_balance_allowance() -> tuple[HealthcheckResult, HealthcheckResult]:
    min_usdc = EARLY_REVERSION_POSITION_SIZE_USDC
    if not wallet_configured():
        missing = HealthcheckResult(
            "USDC balance",
            False,
            "POLY_PRIVATE_KEY is not set",
        )
        return missing, HealthcheckResult("Allowance", False, "POLY_PRIVATE_KEY is not set")

    try:
        client = get_authenticated_clob_client(force_new=True)
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        client.update_balance_allowance(params)
        response = client.get_balance_allowance(params)
        if not isinstance(response, dict):
            return (
                HealthcheckResult("USDC balance", False, f"unexpected response: {response!r}"),
                HealthcheckResult("Allowance", False, f"unexpected response: {response!r}"),
            )

        balance_usdc = _to_usdc(response.get("balance"))
        allowance_usdc = _parse_allowance_usdc(response)

        balance_ok = balance_usdc >= min_usdc
        allowance_ok = allowance_usdc >= min_usdc

        proxy_hint = ""
        if not balance_ok and not POLY_PROXY_WALLET:
            proxy_hint = " | check POLY_PROXY_WALLET in .env"

        balance = HealthcheckResult(
            "USDC balance",
            balance_ok,
            f"{balance_usdc:.6f} USDC (min {min_usdc:.2f}){proxy_hint}",
        )
        allowance = HealthcheckResult(
            "Allowance",
            allowance_ok,
            f"{allowance_usdc:.6f} USDC (min {min_usdc:.2f}){proxy_hint}",
        )
        return balance, allowance
    except Exception as exc:
        return (
            HealthcheckResult("USDC balance", False, str(exc)),
            HealthcheckResult("Allowance", False, str(exc)),
        )


def _check_can_place_order() -> HealthcheckResult:
    if not wallet_configured():
        return HealthcheckResult("Can place order", False, "POLY_PRIVATE_KEY is not set")

    market = find_active_btc_5m_market()
    if market is None:
        return HealthcheckResult("Can place order", False, "no active BTC 5m market")

    token_id = None
    price = None
    for quotes in (market.yes_quotes, market.no_quotes):
        if quotes.ask is not None and quotes.ask > 0:
            token_id = quotes.token_id
            price = quotes.ask
            break

    if token_id is None or price is None:
        return HealthcheckResult("Can place order", False, "no valid ask on active market")

    min_shares = EARLY_REVERSION_POSITION_SIZE_USDC / price
    try:
        client = get_authenticated_clob_client(force_new=True)
        signed_order = client.create_order(
            OrderArgs(
                token_id=token_id,
                price=price,
                size=min_shares,
                side=BUY,
            )
        )
        if signed_order is None:
            return HealthcheckResult("Can place order", False, "create_order returned None")
        return HealthcheckResult(
            "Can place order",
            True,
            f"signed test order for {market.slug} @ {price:.4f}",
        )
    except Exception as exc:
        return HealthcheckResult("Can place order", False, str(exc))


def run_healthchecks() -> list[HealthcheckResult]:
    wallet = _check_wallet()
    balance, allowance = _fetch_collateral_balance_allowance()
    api_creds = _check_api_creds()
    can_place = _check_can_place_order()
    return [wallet, balance, allowance, api_creds, can_place]


def format_report(results: list[HealthcheckResult]) -> str:
    lines = ["=== CLOB Healthcheck ===", ""]
    for result in results:
        line = f"{result.name}: {_status(result.passed)}"
        if result.details:
            line = f"{line} — {result.details}"
        lines.append(line)

    overall = all(item.passed for item in results)
    lines.extend(["", f"OVERALL: {_status(overall)}"])
    return "\n".join(lines)


def print_report() -> bool:
    results = run_healthchecks()
    print(format_report(results))
    return all(item.passed for item in results)


def main() -> None:
    ok = print_report()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
