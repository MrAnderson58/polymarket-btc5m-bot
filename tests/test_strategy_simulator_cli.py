"""CLI regression tests for strategy_simulator argparse construction."""

from __future__ import annotations

import argparse
import inspect
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

from bot.research.strategy_simulator import __main__ as cli_main
from bot.research.strategy_simulator.__main__ import MARKET_FILTER_OPTIONS, build_parser
from bot.research.strategy_simulator.market_filter import (
    FORWARD_TRACK_MARKET_START_TS_HELP,
    add_market_filter_args,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SUBCOMMANDS = (
    "quote-audit",
    "dense-era",
    "discover",
    "discover-v2",
    "archetype-report",
    "simulate",
    "walk-forward",
    "split-diagnostics",
    "prepare-forward",
    "forward-track",
    "finalists",
)

MINIMAL_ARGS: dict[str, list[str]] = {
    "quote-audit": ["quote-audit"],
    "dense-era": ["dense-era"],
    "discover": ["discover", "--no-persist", "--no-progress"],
    "discover-v2": ["discover-v2", "--no-progress", "--max-markets", "5"],
    "archetype-report": ["archetype-report"],
    "simulate": ["simulate", "--no-persist"],
    "walk-forward": ["walk-forward", "--no-progress"],
    "split-diagnostics": ["split-diagnostics", "--no-progress"],
    "prepare-forward": ["prepare-forward"],
    "forward-track": ["forward-track"],
    "finalists": ["finalists"],
}

SUBPROCESS_SMOKE = (
    ["--help"],
    ["quote-audit", "--sample-markets", "1"],
    ["dense-era"],
    ["discover", "--help"],
    ["discover-v2", "--help"],
    ["archetype-report"],
    ["simulate", "--help"],
    ["walk-forward", "--help"],
    ["split-diagnostics", "--help"],
    ["prepare-forward", "--help"],
    ["forward-track", "--help"],
    ["finalists", "--help"],
)


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


def _run_cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bot.research.strategy_simulator", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class StrategySimulatorCliTestCase(unittest.TestCase):
    def test_add_market_filter_args_accepts_market_start_ts_help(self) -> None:
        sig = inspect.signature(add_market_filter_args)
        self.assertIn("market_start_ts_help", sig.parameters)
        parser = argparse.ArgumentParser()
        add_market_filter_args(parser, market_start_ts_help="custom help")
        action = next(a for a in parser._actions if "--market-start-ts" in a.option_strings)
        self.assertEqual(action.help, "custom help")

    def test_add_filter_args_signature_matches_market_filter_helper(self) -> None:
        sig = inspect.signature(cli_main._add_filter_args)
        self.assertEqual(
            list(sig.parameters.keys()),
            ["parser", "market_start_ts_help"],
        )
        parser = argparse.ArgumentParser()
        cli_main._add_filter_args(parser, market_start_ts_help="ok")
        action = next(a for a in parser._actions if "--market-start-ts" in a.option_strings)
        self.assertEqual(action.help, "ok")

    def test_build_parser_constructs_without_error(self) -> None:
        parser = build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_build_parser_is_production_entrypoint(self) -> None:
        from bot.research.strategy_simulator.__main__ import build_parser as production_build_parser

        parser = production_build_parser()
        self.assertIsNotNone(parser)

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
            if cmd == "shadow-enable":
                continue
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
                self.assertEqual(action.help, FORWARD_TRACK_MARKET_START_TS_HELP)
                return
        self.fail("--market-start-ts not found on forward-track")

    def test_help_does_not_raise(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)


class StrategySimulatorCliSubprocessTestCase(unittest.TestCase):
    def test_subprocess_help_exits_zero(self) -> None:
        proc = _run_cli(["--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("quote-audit", proc.stdout)

    def test_subprocess_smoke_commands(self) -> None:
        for argv in SUBPROCESS_SMOKE:
            with self.subTest(argv=argv):
                proc = _run_cli(argv)
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"stderr={proc.stderr}\nstdout={proc.stdout}",
                )

    def test_subprocess_quote_audit_and_dense_era(self) -> None:
        for argv in (["quote-audit", "--sample-markets", "1"], ["dense-era"]):
            with self.subTest(argv=argv):
                proc = _run_cli(argv)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.strip())


if __name__ == "__main__":
    unittest.main()
