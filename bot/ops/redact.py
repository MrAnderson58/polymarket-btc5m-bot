"""Secret redaction for operational reports — no credentials in stdout."""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

SECRET_ENV_SUBSTRINGS = (
    "TOKEN",
    "KEY",
    "PASSWORD",
    "SECRET",
    "PRIVATE",
    "CREDENTIAL",
)

SECRET_LINE_PATTERNS = (
    re.compile(r"(?i)(token|api[_-]?key|secret|password|private[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)Bearer\s+\S+"),
    re.compile(r"(?i)postgresql://[^:]+:[^@]+@"),
    re.compile(r"(?i)postgres://[^:]+:[^@]+@"),
)


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in SECRET_ENV_SUBSTRINGS)


def redact_text(text: str) -> str:
    out = text
    for pattern in SECRET_LINE_PATTERNS:
        out = pattern.sub(lambda m: m.group(0).split("=")[0] + "=<redacted>", out)
    return out


def redact_database_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username:
            netloc = f"{parsed.username}:***@{netloc}"
        return urlunparse(parsed._replace(netloc=netloc))
    return url


def env_configured(name: str) -> bool:
    import os

    return bool(os.getenv(name, "").strip())
