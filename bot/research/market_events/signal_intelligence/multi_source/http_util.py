"""S44 — shared HTTP helpers for collectors."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def http_get_text(url: str, *, timeout: float = 12.0) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/json, text/html, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def http_get_json(url: str, *, timeout: float = 12.0) -> Any:
    text = http_get_text(url, timeout=timeout)
    return json.loads(text)


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def quote(s: str) -> str:
    return quote_plus(s)


def url_with_query(base: str, params: dict[str, Any]) -> str:
    return f"{base}?{urlencode(params)}"
