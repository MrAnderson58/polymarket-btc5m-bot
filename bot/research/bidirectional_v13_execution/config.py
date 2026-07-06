"""V1.3 execution-aware research configuration."""

from __future__ import annotations

import os

# Base candidate filter — V1.2 best counterfactual from production research
BASE_CANDIDATE_SPEC_NAME = "yes_move15_no5"

PROMOTION_GATES = {
    "min_filled_markets": 150,
    "executable_pf_min": 1.30,
    "oos_pf_min": 1.20,
    "side_pf_min": 1.10,
    "bootstrap_pp_min": 0.90,
    "rolling_windows": 5,
    "rolling_pass_min": 4,
    "max_concentration_pct": 60.0,
    "passive_min_fill_rate": 0.30,
}

MIN_SAMPLE_EXPLORATORY = 30
MIN_SAMPLE_PROMOTABLE = 50

QUOTE_PATH_PRE_SEC = 5
QUOTE_PATH_POST_SEC = 10

# MTF quote-mapping fix deployment (unix ts). Override via MTF_QUOTE_FIX_TS env.
MTF_QUOTE_FIX_TS: int | None = (
    int(os.environ["MTF_QUOTE_FIX_TS"]) if os.environ.get("MTF_QUOTE_FIX_TS") else None
)
