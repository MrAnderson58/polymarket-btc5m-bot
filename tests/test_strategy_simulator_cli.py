"""CLI regression tests for strategy_simulator argparse construction."""

from __future__ import annotations

import argparse
import unittest
from collections import Counter

from bot.research.strategy_simulator.__main__ import MARKET_FILTER_OPTIONS, build_parser

SUBCOMMANDS = (
    "quote-audit",
    "dense-era",
    "discover",
    "simulate",
    "walk-forward",
    "split-diagnostics",
    "prepare-forward",
    "forward-track",
    "finalists",
    "shadow-enable",
)

MINIMAL_ARGS: dict[str, list[str]] = {
    "quote-audit": ["quote-audit"],
    "dense-era": ["dense-era"],
    "discover": ["discover", "--no-persist", "--no-progress"],
    "simulate": ["simulate", "--no-persist"],
    "walk-forward": ["walk-forward", "--no-progress"],
    "split-diagnostics": ["split-diagnostics", "--no-progress"],
    "prepare-forward": ["prepare-forward"],
    "forward-track": ["forward-track"],
    "finalists": ["finalists"],
    "shadow-enable": ["shadow-enable", "--strategy-id", "1", "--force"],
}


def _option_strings(parser: argparse.ArgumentParser) -> list[str]:
    opts: list[str] = []
    for action in parser._actions:
        if action.option_strings:
            opts.extend(action.option_strings)
    return opts


def _subparser_by_name(root: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise KeyError(name)


class StrategySimulatorCliTestCase(unittest.TestCase):
    def test_build_parser_constructs_without_error(self) -> None:
        parser = build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_each_subcommand_parses_minimal_args(self) -> None:
        for cmd in SUBCOMMANDS:
            with self.subTest(command=cmd):
                args = build_parser().parse_args(MINIMAL_ARGS[cmd])
                self.assertEqual(args.command, cmd)

    def test_market_filter_options_registered_once_on_forward_track(self) -> None:
        root = build_parser()
        fwd = _subparser_by_name(root, "forward-track")
        opts = _option_strings(fwd)
        for flag in MARKET_FILTER_OPTIONS:
            self.assertEqual(opts.count(flag), 1, f"{flag} on forward-track")

    def test_market_filter_options_unique_per_filtered_subcommand(self) -> None:
        root = build_parser()
        for cmd in ("simulate", "discover", "walk-forward", "split-diagnostics", "forward-track"):
            sub = _subparser_by_name(root, cmd)
            opts = _option_strings(sub)
            for flag in MARKET_FILTER_OPTIONS:
                self.assertEqual(opts.count(flag), 1, f"{flag} on {cmd}")

    def test_no_duplicate_option_strings_on_any_subparser(self) -> None:
        root = build_parser()
        for cmd in SUBCOMMANDS:
            sub = _subparser_by_name(root, cmd)
            opts = [o for o in _option_strings(sub) if o.startswith("--")]
            counts = Counter(opts)
            dups = [o for o, n in counts.items() if n > 1]
            self.assertEqual(dups, [], f"duplicate options on {cmd}: {dups}")

    def test_forward_track_market_start_ts_help(self) -> None:
        root = build_parser()
        fwd = _subparser_by_name(root, "forward-track")
        for action in fwd._actions:
            if "--market-start-ts" in action.option_strings:
                self.assertIn("dense-era boundary", action.help or "")
                return
        self.fail("--market-start-ts not found on forward-track")

    def test_help_does_not_raise(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
