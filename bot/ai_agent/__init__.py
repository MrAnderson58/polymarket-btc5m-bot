"""Trading AI Agent v1 — observe-only signal intelligence."""

__all__ = [
    "build_daily_report",
    "load_ai_features",
    "run_daily_sync",
    "sync_ai_features",
]


def __getattr__(name: str):
    if name in ("build_daily_report", "run_daily_sync"):
        from bot.ai_agent.daily import build_daily_report, run_daily_sync

        return {"build_daily_report": build_daily_report, "run_daily_sync": run_daily_sync}[name]
    if name in ("load_ai_features", "sync_ai_features"):
        from bot.ai_agent.memory import load_ai_features, sync_ai_features

        return {"load_ai_features": load_ai_features, "sync_ai_features": sync_ai_features}[name]
    raise AttributeError(name)
