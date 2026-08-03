"""Decision thresholds — research gates only (no new features)."""

from __future__ import annotations

# Fingerprint similarity uses 100/(1+euclidean); typical corpus sits ~5–20%.
MIN_FINGERPRINT_SIM = 5.0
MIN_TIMELINE_SIM = 30.0
MIN_HIST_WR = 55.0
MIN_HIST_EV = 0.15
MIN_HIST_PF = 1.15
MIN_CONFIDENCE = 0.45
MIN_DIRECTIONAL_VOTES = 2
MIN_SUPPORTING_MODULES = 2

# Replay/causality required only when libraries are dense enough.
REQUIRE_REPLAY_OR_CAUSAL = True
MIN_REPLAY_LIBRARY = 500
