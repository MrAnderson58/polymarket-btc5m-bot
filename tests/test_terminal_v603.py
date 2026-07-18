"""V6.0.3 — Event Bus & Command Pipeline tests (no live opens)."""

from __future__ import annotations

import unittest

from bot.terminal.commands import (
    ClosePositionCommand,
    CommandDispatcher,
    OpenPositionCommand,
    get_command_dispatcher,
)
from bot.terminal.events import (
    EventBus,
    PositionOpened,
    PositionRequested,
    SignalViewed,
    reset_event_bus,
)
from bot.terminal.execution import ExecutionResult, ExecutionService, PaperAdapter
from bot.terminal.execution.models import OpenPositionRequest


class TestEventBusV603(unittest.TestCase):
    def test_publish_invokes_subscriber(self) -> None:
        bus = EventBus()
        seen: list[SignalViewed] = []

        def handler(event: SignalViewed) -> None:
            seen.append(event)

        bus.subscribe(SignalViewed, handler)
        bus.publish(SignalViewed(symbol="BTC"))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].symbol, "BTC")

    def test_unsubscribe_works(self) -> None:
        bus = EventBus()
        seen: list[SignalViewed] = []

        def handler(event: SignalViewed) -> None:
            seen.append(event)

        bus.subscribe(SignalViewed, handler)
        bus.unsubscribe(SignalViewed, handler)
        bus.publish(SignalViewed(symbol="ETH"))
        self.assertEqual(seen, [])

    def test_events_construct(self) -> None:
        e = PositionRequested(symbol="BTC", side="LONG")
        self.assertEqual(e.symbol, "BTC")
        self.assertEqual(e.side, "LONG")
        opened = PositionOpened(symbol="BTC", side="SHORT")
        self.assertEqual(opened.side, "SHORT")


class _AlwaysOkAdapter(PaperAdapter):
    """Test double — does not open real trades."""

    def open_position(self, request: OpenPositionRequest) -> ExecutionResult:
        return ExecutionResult(
            ok=True,
            operation="open_position",
            code="ok",
            message="test-ok",
            data={"symbol": request.symbol, "side": request.side},
        )


class TestCommandDispatcherV603(unittest.TestCase):
    def setUp(self) -> None:
        reset_event_bus()

    def test_dispatcher_accepts_command(self) -> None:
        bus = EventBus()
        requested: list[PositionRequested] = []
        bus.subscribe(PositionRequested, requested.append)
        disp = CommandDispatcher(execution=ExecutionService(PaperAdapter()), bus=bus)
        result = disp.dispatch(OpenPositionCommand(symbol="BTC", side="LONG"))
        self.assertFalse(result.ok)  # paper gated
        self.assertEqual(result.code, "not_enabled")
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0].symbol, "BTC")

    def test_dispatcher_publishes_opened_when_ok(self) -> None:
        bus = EventBus()
        opened: list[PositionOpened] = []
        bus.subscribe(PositionOpened, opened.append)
        disp = CommandDispatcher(execution=ExecutionService(_AlwaysOkAdapter()), bus=bus)
        result = disp.dispatch(OpenPositionCommand(symbol="ETH", side="SHORT"))
        self.assertTrue(result.ok)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].symbol, "ETH")

    def test_factory(self) -> None:
        self.assertIsInstance(get_command_dispatcher(), CommandDispatcher)
        close = ClosePositionCommand(symbol="BTC")
        self.assertEqual(close.symbol, "BTC")


if __name__ == "__main__":
    unittest.main()
