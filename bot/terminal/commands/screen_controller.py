"""ScreenController — builds Terminal replies (services live here, not in Router)."""

from __future__ import annotations

from typing import Any

from bot.terminal.events.event_bus import get_event_bus
from bot.terminal.events.events import PortfolioViewed, SignalViewed
from bot.terminal.session import get_session_manager
from bot.terminal.telemetry import get_telemetry
from bot.terminal.timeline import get_timeline_service
from bot.terminal.watchlist import normalize_symbol


def _kb():
    from bot.terminal.telegram import keyboards

    return keyboards


def _render():
    from bot.terminal.telegram import render

    return render


class ScreenController:
    """Owns service calls + session/timeline/telemetry updates for UI screens."""

    def navigate(
        self,
        *,
        screen: str,
        chat_id: str,
        symbol: str | None = None,
        args: tuple[str, ...] = (),
        edit: bool = True,
    ) -> dict[str, Any]:
        """Return dict compatible with TerminalReply fields."""
        screen = (screen or "home").lower()
        tele = get_telemetry()
        sessions = get_session_manager()
        timeline = get_timeline_service()

        if symbol:
            symbol = normalize_symbol(symbol)

        # --- actions with side effects ---
        if screen in {"watch_add", "add_watch"} and symbol:
            from bot.terminal.watchlist import get_watchlist_service

            get_watchlist_service().add(chat_id, symbol)
            timeline.record(symbol, "watchlist", "Added to watchlist")
            sessions.update_session(chat_id, screen="watch", symbol=symbol)
            tele.record_screen("watch")
            return self._watch(chat_id, edit=edit, notice=f"Added {symbol}")

        if screen in {"watch_remove", "remove_watch"} and symbol:
            from bot.terminal.watchlist import get_watchlist_service

            get_watchlist_service().remove(chat_id, symbol)
            timeline.record(symbol, "watchlist", "Removed from watchlist")
            sessions.update_session(chat_id, screen="watch", symbol=symbol)
            tele.record_screen("watch")
            return self._watch(chat_id, edit=edit, notice=f"Removed {symbol}")

        if screen == "decision":
            return self._decision(chat_id, symbol=symbol, edit=edit)

        if screen in {"why", "explain"}:
            return self._why(chat_id, symbol=symbol, edit=edit)

        if screen in {"research", "review"}:
            return self._research(chat_id, symbol=symbol, args=args, edit=edit)

        if screen == "brief":
            return self._brief(chat_id, args=args, edit=edit)

        if screen == "timeline":
            return self._timeline(chat_id, symbol=symbol, edit=edit)

        if screen == "signals":
            return self._signals(chat_id, edit=edit)

        if screen == "portfolio":
            return self._portfolio(chat_id, edit=edit)

        if screen in {"watch", "watchlist", "favorites"}:
            return self._watch(chat_id, edit=edit)

        if screen in {"alert", "alerts"}:
            return self._alerts(chat_id, edit=edit, args=args)

        if screen == "markets":
            return self._markets(chat_id, edit=edit)

        if screen == "positions":
            return self._positions(chat_id, edit=edit)

        if screen == "account":
            return self._account(chat_id, edit=edit)

        if screen == "settings":
            sessions.update_session(chat_id, screen="settings")
            tele.record_screen("settings")
            return {
                "text": _render().render_settings(),
                "reply_markup": _kb().flow_keyboard(symbol=sessions.get_session(chat_id).symbol),
                "edit": edit,
                "ok": True,
            }

        if screen in {"start"}:
            sessions.update_session(chat_id, screen="home")
            tele.record_screen("start")
            from bot.terminal.services.system_service import get_system_service

            return {
                "text": _render().render_start(get_system_service().get_home()),
                "reply_markup": _kb().home_keyboard(),
                "edit": False,
                "ok": True,
            }

        if screen in {"home", "back"}:
            sessions.update_session(chat_id, screen="home")
            tele.record_screen("home")
            from bot.terminal.services.system_service import get_system_service

            return {
                "text": _render().render_home(get_system_service().get_home()),
                "reply_markup": _kb().home_keyboard(),
                "edit": True,
                "ok": True,
            }

        # default home
        sessions.update_session(chat_id, screen="home")
        tele.record_screen(screen or "home")
        from bot.terminal.services.system_service import get_system_service

        return {
            "text": _render().render_home(get_system_service().get_home()),
            "reply_markup": _kb().home_keyboard(),
            "edit": True,
            "ok": True,
        }

    def toggle_watch(self, *, chat_id: str, symbol: str, edit: bool = True) -> dict[str, Any]:
        from bot.terminal.watchlist import get_watchlist_service

        sym = normalize_symbol(symbol)
        _wl, now_on = get_watchlist_service().toggle(chat_id, sym)
        get_timeline_service().record(
            sym, "watchlist", "Added to watchlist" if now_on else "Removed from watchlist"
        )
        get_session_manager().update_session(chat_id, symbol=sym)
        notice = f"{'★ Added' if now_on else '☆ Removed'} {sym}"
        # Stay on decision if that was last screen
        session = get_session_manager().get_session(chat_id)
        if session.screen == "decision":
            return self._decision(chat_id, symbol=sym, edit=edit, notice=notice)
        return self._signals(chat_id, edit=edit, notice=notice)

    def _signals(self, chat_id: str, *, edit: bool, notice: str | None = None) -> dict[str, Any]:
        from bot.terminal.services.signals_service import get_signal_service
        from bot.terminal.watchlist import get_watchlist_service

        get_event_bus().publish(SignalViewed(source="telegram"))
        tele = get_telemetry()
        with tele.measure_scan():
            signals = get_signal_service().get_top(limit=5)
        tele.record_signals_viewed(len(signals))
        tele.record_screen("signals")
        symbols = [s.symbol for s in signals if s.symbol and s.symbol != "—"]
        for sym in symbols:
            get_timeline_service().record(sym, "signal", "Signal generated")
        favs = set(get_watchlist_service().list_symbols(chat_id))
        get_session_manager().update_session(chat_id, screen="signals")
        text = _render().render_signals(signals)
        if notice:
            text = f"{text}\n\n{notice}"
        return {
            "text": text,
            "reply_markup": _kb().signals_flow_keyboard(symbols, favorited=favs),
            "edit": edit,
            "ok": True,
        }

    def _decision(
        self,
        chat_id: str,
        *,
        symbol: str | None,
        edit: bool,
        notice: str | None = None,
    ) -> dict[str, Any]:
        from bot.terminal.decision import get_decision_service

        tele = get_telemetry()
        sym = symbol or get_session_manager().get_session(chat_id).symbol or "BTC"
        with tele.measure_decision():
            card = get_decision_service().for_symbol(sym)
        tele.record_decision_opened()
        tele.record_screen("decision")
        if card is None:
            get_session_manager().update_session(chat_id, screen="decision", symbol=sym)
            return {
                "text": _render().render_decision(None, notice="No DecisionCard — try another symbol"),
                "reply_markup": _kb().flow_keyboard(symbol=sym),
                "edit": edit,
                "ok": True,
            }
        get_timeline_service().record(card.symbol, "decision", "Decision updated")
        snapshot = {
            "symbol": card.symbol,
            "direction": card.direction,
            "score": card.score,
            "confidence": card.confidence,
        }
        get_session_manager().update_session(
            chat_id,
            screen="decision",
            symbol=card.symbol,
            last_decision=snapshot,
        )
        text = _render().render_decision(card)
        if notice:
            text = f"{text}\n\n{notice}"
        return {
            "text": text,
            "reply_markup": _kb().decision_keyboard(card.symbol),
            "edit": edit,
            "ok": True,
        }

    def _why(self, chat_id: str, *, symbol: str | None, edit: bool) -> dict[str, Any]:
        from bot.terminal.explain import why_symbol

        sym = symbol or get_session_manager().get_session(chat_id).symbol or "BTC"
        expl = why_symbol(sym)
        get_session_manager().update_session(chat_id, screen="why", symbol=sym)
        get_telemetry().record_screen("why")
        if expl is None:
            text = f"No scanner score for {sym}"
        else:
            text = _render().render_why(expl)
        return {
            "text": text,
            "reply_markup": _kb().flow_keyboard(symbol=sym),
            "edit": edit,
            "ok": True,
        }

    def _research(
        self,
        chat_id: str,
        *,
        symbol: str | None,
        args: tuple[str, ...],
        edit: bool,
    ) -> dict[str, Any]:
        from bot.terminal.research import format_research_review, get_research_service

        sym = symbol or get_session_manager().get_session(chat_id).symbol
        if not sym:
            return {
                "text": "Usage: pick a symbol — /research BTC",
                "reply_markup": _kb().flow_keyboard(),
                "edit": edit,
                "ok": True,
            }
        use_claude = not any(a.lower() in {"template", "local"} for a in args)
        review = get_research_service().review_symbol(sym, use_claude=use_claude)
        get_timeline_service().record(sym, "research", "Research reviewed")
        get_session_manager().update_session(chat_id, screen="research", symbol=sym)
        get_telemetry().record_screen("research")
        return {
            "text": format_research_review(review),
            "reply_markup": _kb().flow_keyboard(symbol=sym),
            "edit": edit,
            "ok": True,
        }

    def _brief(self, chat_id: str, *, args: tuple[str, ...], edit: bool) -> dict[str, Any]:
        from bot.terminal.brief import get_morning_brief_service
        from bot.terminal.brief.builder import format_morning_brief

        enrich = any(a.lower() in {"ai", "claude", "enrich"} for a in args)
        brief = get_morning_brief_service().build(user_id=chat_id)
        if enrich:
            from bot.terminal.research import get_research_service

            brief = get_research_service().enrich_brief(brief, user_id=chat_id, use_claude=True)
        get_session_manager().update_session(chat_id, screen="brief")
        get_telemetry().record_screen("brief")
        return {
            "text": format_morning_brief(brief),
            "reply_markup": _kb().flow_keyboard(
                symbol=get_session_manager().get_session(chat_id).symbol
            ),
            "edit": edit,
            "ok": True,
        }

    def _timeline(self, chat_id: str, *, symbol: str | None, edit: bool) -> dict[str, Any]:
        sym = symbol or get_session_manager().get_session(chat_id).symbol or "BTC"
        events = get_timeline_service().for_symbol(sym, limit=20)
        get_session_manager().update_session(chat_id, screen="timeline", symbol=sym)
        get_telemetry().record_screen("timeline")
        return {
            "text": _render().render_timeline(sym, events),
            "reply_markup": _kb().flow_keyboard(symbol=sym),
            "edit": edit,
            "ok": True,
        }

    def _portfolio(self, chat_id: str, *, edit: bool) -> dict[str, Any]:
        from bot.terminal.portfolio import get_portfolio_intelligence_service

        get_event_bus().publish(PortfolioViewed(source="telegram"))
        intel = get_portfolio_intelligence_service().analyze()
        sym = get_session_manager().get_session(chat_id).symbol
        if sym:
            get_timeline_service().record(sym, "portfolio", "Portfolio advice changed")
        get_session_manager().update_session(chat_id, screen="portfolio")
        get_telemetry().record_screen("portfolio")
        return {
            "text": _render().render_portfolio_intelligence(intel),
            "reply_markup": _kb().flow_keyboard(symbol=sym),
            "edit": edit,
            "ok": True,
        }

    def _watch(self, chat_id: str, *, edit: bool, notice: str | None = None) -> dict[str, Any]:
        from bot.terminal.watchlist import get_watchlist_service

        symbols = list(get_watchlist_service().list_symbols(chat_id))
        get_session_manager().update_session(chat_id, screen="watch")
        get_telemetry().record_screen("watch")
        return {
            "text": _render().render_watchlist(symbols, notice=notice),
            "reply_markup": _kb().watchlist_keyboard(symbols),
            "edit": edit,
            "ok": True,
        }

    def _alerts(
        self,
        chat_id: str,
        *,
        edit: bool,
        notice: str | None = None,
        args: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        from bot.terminal.alerts import get_alert_service

        svc = get_alert_service()
        if args:
            action = args[0].lower()
            if action == "add":
                if len(args) < 2:
                    notice = "Usage: /alert add BTC score>85"
                else:
                    try:
                        rule = svc.parse_and_create(chat_id, list(args[1:]))
                        notice = f"Rule created\n{rule.symbol}\n{rule.describe()}"
                    except ValueError as exc:
                        notice = str(exc)
            elif action in {"remove", "rm", "del", "delete"}:
                if len(args) < 2:
                    notice = "Usage: /alert remove <id>"
                else:
                    ok = svc.remove(args[1])
                    notice = "Removed" if ok else "Not found"
            elif action not in {"list", "show"}:
                try:
                    rule = svc.parse_and_create(chat_id, list(args))
                    notice = f"Rule created\n{rule.symbol}\n{rule.describe()}"
                except ValueError as exc:
                    notice = str(exc)

        rules = svc.list(chat_id)
        get_session_manager().update_session(chat_id, screen="alerts")
        get_telemetry().record_screen("alerts")
        return {
            "text": _render().render_alerts(rules, notice=notice),
            "reply_markup": _kb().flow_keyboard(),
            "edit": edit,
            "ok": True,
        }

    def _markets(self, chat_id: str, *, edit: bool) -> dict[str, Any]:
        from bot.terminal.services.market_service import get_market_service

        get_session_manager().update_session(chat_id, screen="markets")
        get_telemetry().record_screen("markets")
        return {
            "text": _render().render_markets(get_market_service().get_markets()),
            "reply_markup": _kb().flow_keyboard(),
            "edit": edit,
            "ok": True,
        }

    def _positions(self, chat_id: str, *, edit: bool) -> dict[str, Any]:
        from bot.terminal.services.positions_service import get_position_service

        get_session_manager().update_session(chat_id, screen="positions")
        get_telemetry().record_screen("positions")
        return {
            "text": _render().render_positions(get_position_service().get_open()),
            "reply_markup": _kb().flow_keyboard(),
            "edit": edit,
            "ok": True,
        }

    def _account(self, chat_id: str, *, edit: bool) -> dict[str, Any]:
        from bot.terminal.services.account_service import get_account_service

        get_session_manager().update_session(chat_id, screen="account")
        get_telemetry().record_screen("account")
        return {
            "text": _render().render_account(get_account_service().get_balance()),
            "reply_markup": _kb().flow_keyboard(),
            "edit": edit,
            "ok": True,
        }

    def execution_stub(self, *, edit: bool) -> dict[str, Any]:
        return {
            "text": _render().render_execution_stub(),
            "reply_markup": _kb().flow_keyboard(),
            "edit": edit,
            "ok": True,
        }


_controller: ScreenController | None = None


def get_screen_controller() -> ScreenController:
    global _controller
    if _controller is None:
        _controller = ScreenController()
    return _controller


def reset_screen_controller() -> None:
    global _controller
    _controller = None


__all__ = ["ScreenController", "get_screen_controller", "reset_screen_controller"]
