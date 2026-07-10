"""AI analyst shadow layer."""

from bot.research.market_events.ai_analyst.analysis_runner import run_analysis_job
from bot.research.market_events.ai_analyst.context_bundle import build_context_bundle
from bot.research.market_events.ai_analyst.job_queue import (
    enqueue_analysis_job,
    process_pending_jobs,
    start_background_worker,
)
from bot.research.market_events.ai_analyst.provider import get_analyst_provider

__all__ = [
    "build_context_bundle",
    "enqueue_analysis_job",
    "get_analyst_provider",
    "process_pending_jobs",
    "run_analysis_job",
    "start_background_worker",
]
