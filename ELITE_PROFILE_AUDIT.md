# ELITE_PROFILE_AUDIT

_Elite Profile Audit V1 — research only._

- runtime: **2.219s**
- closed: 19205
- elite/A+/A: 1204/1659/1248
- ignore: 15094
- stored_equals_accepted: True

## Explain
Elite+A++A equals Book B accepted count.

## Extremes
- SOL: SOL share of elite=5.72% (n=235). SOL elite WR=84.68. If report showed ~90%, that is WR (or a single-coin WR slice), not '90% of elite are SOL'.
- SHORT: SHORT dominance elite=98.13% tracks Decision accepted SHORT=98.13% (corpus=49.71%). Profile inherits Book B accept mix — not an independent profile bug.
- Friday: Friday elite pct=20.04. Sat=45.22% of elite — elevated but not 100%. Compare vs Decision accepted weekday mix.
- WR: Elite overall WR=76.23 n=4111 pf=None. WR≈100% / PF=None appears on slices with zero losses (pf infinite). Check per-coin WR in COIN_AUDIT — not global elite WR unless all elite are winners.

## Sampling
```json
{
  "source": "elite_candidates_v1 + market_decision_journal_v1(book=paper_decision) + research_lake",
  "where": [
    "elite_candidates_v1.category IN ('ELITE','A+','A')",
    "journal.book = 'paper_decision'",
    "ignore := journal.trade_id NOT IN elite_store.trade_id"
  ],
  "joins": [
    "LEFT enrich elite/ignore <- journal ON trade_id (timeline/fingerprint/pnl)",
    "LEFT enrich <- research_lake ON trade_id (ATR/ADX/RSI/MACD/funding/OI/...)"
  ],
  "filters": [
    "STORE_CATEGORIES only for portrait corpus",
    "no LIMIT on elite load (optional engine limit unused in profile)",
    "no date filter",
    "no status filter beyond closed pnl when available",
    "no ORDER BY affecting membership (ORDER BY score DESC for display only)"
  ],
  "limit": null,
  "order_by": "score DESC, opened_at DESC (display/store load only)",
  "date_filters": [],
  "book_filters": [
    "book=paper_decision"
  ],
  "status_filters": [],
  "candidate_filters": [
    "category in ['A', 'A+', 'ELITE']"
  ],
  "hidden_filters": [],
  "note": "No hidden filters. Profile intentionally excludes B and IGNORE from portrait."
}
```
