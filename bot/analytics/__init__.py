"""Analytics blocks for Report v3/v4."""

from bot.analytics.heatmap import build_heatmaps, enrich_heatmaps_from_report
from bot.analytics.intelligence import build_trading_intelligence
from bot.analytics.live_sample import build_live_sample
from bot.analytics.overfit import build_overfit_detector
from bot.analytics.sensitivity import build_sensitivity_analysis
from bot.analytics.stability import build_parameter_stability

__all__ = [
    "build_heatmaps",
    "build_live_sample",
    "build_overfit_detector",
    "build_parameter_stability",
    "build_sensitivity_analysis",
    "build_trading_intelligence",
    "enrich_heatmaps_from_report",
]
