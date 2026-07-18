"""V6.0.2 — Execution abstraction (Paper only) smoke tests — no live opens."""

from __future__ import annotations

import unittest

from bot.terminal.execution import (
    ClosePositionRequest,
    ExecutionService,
    OpenPositionRequest,
    PaperAdapter,
    get_execution_service,
)
from bot.terminal.telegram.terminal_router import dispatch_terminal_command
from bot.terminal.telegram.render import render_execution_stub


class TestTerminalExecutionV602(unittest.TestCase):
    def test_open_position_request(self) -> None:
        req = OpenPositionRequest(symbol="BTC", side="LONG", size=100.0)
        self.assertEqual(req.symbol, "BTC")
        self.assertEqual(req.side, "LONG")

    def test_close_position_request(self) -> None:
        req = ClosePositionRequest(symbol="ETH", side="SHORT")
        self.assertEqual(req.symbol, "ETH")
        self.assertEqual(req.side, "SHORT")

    def test_execution_service_imports(self) -> None:
        self.assertTrue(callable(get_execution_service))
        self.assertTrue(issubclass(ExecutionService, object))

    def test_paper_adapter_constructs(self) -> None:
        adapter = PaperAdapter()
        self.assertEqual(adapter.name, "paper")
        svc = ExecutionService(adapter)
        self.assertIsInstance(svc, ExecutionService)

    def test_open_gated_no_trade(self) -> None:
        svc = get_execution_service(live=False)
        result = svc.open_position(OpenPositionRequest(symbol="BTC", side="LONG"))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_enabled")

    def test_close_gated_no_trade(self) -> None:
        svc = get_execution_service()
        result = svc.close_position(ClosePositionRequest(symbol="BTC"))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_enabled")

    def test_telegram_execution_stubs(self) -> None:
        stub = render_execution_stub()
        self.assertIn("Execution API ready", stub)
        self.assertIn("Live execution not enabled", stub)
        self.assertIn("Paper adapter available", stub)
        for cmd in ("/open", "/close", "/risk", "/sl", "/tp"):
            reply = dispatch_terminal_command(cmd)
            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("Execution API ready", reply.text)

    def test_live_factory_blocked(self) -> None:
        with self.assertRaises(RuntimeError):
            get_execution_service(live=True)


if __name__ == "__main__":
    unittest.main()
