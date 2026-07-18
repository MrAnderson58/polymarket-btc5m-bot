"""System/home aggregation from existing process_manager + paper/signals APIs."""

from __future__ import annotations

from typing import Protocol

from bot.terminal.models.dto import HomeCard, SystemStatusCard, WorkerStatusCard
from bot.terminal.services.account_service import get_account_service
from bot.terminal.services.portfolio_service import get_portfolio_service
from bot.terminal.services.signals_service import get_signal_service


class SystemService(Protocol):
    def get_status(self) -> SystemStatusCard:
        ...

    def get_home(self) -> HomeCard:
        ...


class DefaultSystemService:
    def get_status(self) -> SystemStatusCard:
        workers: list[WorkerStatusCard] = []
        dashboard = "—"
        telegram = "—"
        health_text = ""
        try:
            from bot.research.market_events.process_manager import system_health_report

            health_text = system_health_report()
        except Exception as exc:
            health_text = f"health unavailable: {exc}"

        try:
            from bot.research.market_events.process_manager import (
                DASHBOARD_API_HOST,
                DASHBOARD_API_PORT,
                SERVICES,
                find_service_processes,
            )

            online_any = False
            for svc in SERVICES:
                procs = find_service_processes(svc)
                is_up = bool(procs)
                online_any = online_any or is_up
                detail = f"PID{procs[0].pid}" if procs else "down"
                workers.append(WorkerStatusCard(name=svc.label, online=is_up, detail=detail))
                if svc.key == "dashboard":
                    dashboard = (
                        f"ONLINE http://{DASHBOARD_API_HOST}:{DASHBOARD_API_PORT}"
                        if is_up
                        else "OFFLINE"
                    )
                if svc.key == "telegram":
                    telegram = "ONLINE" if is_up else "OFFLINE"

            return SystemStatusCard(
                online=online_any,
                workers=tuple(workers),
                dashboard=dashboard,
                telegram=telegram,
                learning=self._line_from_health(health_text, "Learning"),
                decision=self._line_from_health(health_text, "Decision"),
                pattern=self._line_from_health(health_text, "Pattern"),
                news=self._line_from_health(health_text, "News"),
                summary="process_manager + system_health_report",
            )
        except Exception as exc:
            return SystemStatusCard(
                online=False,
                workers=tuple(workers),
                dashboard=dashboard,
                telegram=telegram,
                learning=self._line_from_health(health_text, "Learning"),
                decision=self._line_from_health(health_text, "Decision"),
                pattern=self._line_from_health(health_text, "Pattern"),
                news=self._line_from_health(health_text, "News"),
                summary=str(exc),
            )

    @staticmethod
    def _line_from_health(text: str, key: str) -> str:
        for line in (text or "").splitlines():
            if key in line:
                return line.strip()
        return f"{key}: see health"

    def get_home(self) -> HomeCard:
        status = self.get_status()
        account = get_account_service().get_balance()
        portfolio = get_portfolio_service().get_summary()
        top = get_signal_service().get_top(limit=1)
        best = "—"
        if top and top[0].status != "unavailable":
            s = top[0]
            conf = f"{s.confidence:.1f}" if s.confidence is not None else "—"
            best = f"{s.symbol} {s.direction} conf={conf}"
        last_decision = best if best != "—" else "—"
        workers_up = sum(1 for w in status.workers if w.online)
        workers_line = f"{workers_up}/{len(status.workers)} online"
        return HomeCard(
            system_online=status.online,
            mode=account.mode,
            equity=portfolio.equity,
            balance=account.balance if account.balance is not None else portfolio.equity,
            open_positions=portfolio.open_positions,
            today_pnl=portfolio.today_pnl,
            best_signal=best,
            last_ai_decision=last_decision,
            workers_line=workers_line,
            dashboard_line=status.dashboard,
            system=status,
            summary="live terminal home",
        )


def get_system_service() -> SystemService:
    return DefaultSystemService()


__all__ = ["DefaultSystemService", "SystemService", "get_system_service"]
