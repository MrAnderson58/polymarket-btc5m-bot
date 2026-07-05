"""HTTP helpers for MTF discovery — bounded timeouts, no infinite retries."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# (connect_timeout_sec, read_timeout_sec)
DEFAULT_HTTP_TIMEOUT = (3.0, 10.0)
MAX_HTTP_ATTEMPTS = 1


def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: tuple[float, float] = DEFAULT_HTTP_TIMEOUT,
    max_attempts: int = MAX_HTTP_ATTEMPTS,
) -> tuple[Any | None, str | None]:
    """GET JSON with bounded timeout. Returns (payload, reason_code)."""
    last_reason = "HTTP_REQUEST_FAILED"
    for attempt in range(max(1, max_attempts)):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json(), None
        except requests.Timeout:
            last_reason = "HTTP_TIMEOUT"
            logger.warning("HTTP timeout url=%s attempt=%s", url, attempt + 1)
        except requests.RequestException as exc:
            last_reason = "HTTP_REQUEST_FAILED"
            logger.warning("HTTP failed url=%s attempt=%s: %s", url, attempt + 1, exc)
    return None, last_reason
