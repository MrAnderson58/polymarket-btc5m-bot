"""Parser version factory — v1 preserved, v2 gated."""

from __future__ import annotations

from bot.research.futures.config import PARSER_VERSION
from bot.research.futures.parser import SignalParser, parse_signal_text
from bot.research.futures.parser_v2 import PARSER_VERSION_V2, SignalParserV2, parse_signal_v2

SUPPORTED_VERSIONS = (PARSER_VERSION, PARSER_VERSION_V2)


def get_parser(version: str | None = None):
    ver = version or PARSER_VERSION
    if ver == PARSER_VERSION_V2:
        return SignalParserV2()
    if ver == PARSER_VERSION:
        return SignalParser()
    raise ValueError(f"Unsupported parser version: {ver}")


def parse_message(text: str, *, version: str | None = None):
    ver = version or PARSER_VERSION
    if ver == PARSER_VERSION_V2:
        return parse_signal_v2(text)
    return parse_signal_text(text)
