"""Authenticated Polymarket CLOB client for live trading."""

from __future__ import annotations

import logging

from py_clob_client_v2 import ClobClient

from bot.config import (
    CHAIN_ID,
    CLOB_HOST,
    POLY_PRIVATE_KEY,
    POLY_PROXY_WALLET,
    POLY_SIGNATURE_TYPE,
)

logger = logging.getLogger(__name__)

_client: ClobClient | None = None


def wallet_configured() -> bool:
    return bool(POLY_PRIVATE_KEY)


def get_authenticated_clob_client(*, force_new: bool = False) -> ClobClient:
    global _client
    if _client is not None and not force_new:
        return _client

    if not POLY_PRIVATE_KEY:
        raise RuntimeError("POLY_PRIVATE_KEY is not configured")

    kwargs: dict = {
        "host": CLOB_HOST,
        "chain_id": CHAIN_ID,
        "key": POLY_PRIVATE_KEY,
        "signature_type": POLY_SIGNATURE_TYPE,
    }
    if POLY_PROXY_WALLET:
        kwargs["funder"] = POLY_PROXY_WALLET

    client = ClobClient(**kwargs)
    client.set_api_creds(client.create_or_derive_api_key())
    _client = client
    return client


def verify_authentication() -> tuple[bool, str]:
    if not wallet_configured():
        return False, "POLY_PRIVATE_KEY is not set"
    try:
        client = get_authenticated_clob_client(force_new=True)
        address = client.get_address()
        if not address:
            return False, "CLOB client returned empty wallet address"
        return True, f"wallet={address}"
    except Exception as exc:
        logger.debug("CLOB authentication check failed", exc_info=True)
        return False, str(exc)
