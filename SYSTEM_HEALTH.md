# SYSTEM_HEALTH.md

Generated: **2026-07-31 01:10 UTC+3**  
DB: `data/market_events.db` (sqlite, ~850 MB)  
Scope: S42 paper book + S55 features + G31/S40 activity + ML artifacts + logs

---

## Verdict (short)

| Stage | Status | One-liner |
|---|---|---|
| 1. Bot alive? | **Partial** | Collectors / candidates / signals / workers run. **Paper trading is stalled** (0 OPEN, 0 opens/closes today). |
| 2. Data quality | **Poor** | Only **50** closed S42 trades (batch from Jul 27). Confidence **100% NULL**. Regime **100% RANGE**. |
| 3. Features | **Critical** | RSI / EMA / VWAP / news / AI / confidence **~100% empty**. Present fields often **constant placeholders** (ATR=50, funding=54.1). |
| 4. Edge? | **No durable edge** | Overall **PF 0.44 / EV −25.3 / WR 36%**. No confidence/news/pattern slices. Small morning/explore pockets are not reliable. |

**Bottom line:** the stack is producing candidates and blocked feature rows, but the live paper book is not growing, feature vectors are not informative, and the historical 50 trades do not show a robust informational edge. Optimizer / ML / experiments cannot fix a dead/noisy signal path.

---

## Stage 1 — Is the bot alive?

### Activity snapshot

| Metric | Value | Notes |
|---|---|---|
| Trades opened today | **0** | Last open: **2026-07-27 21:12** (all 50 created at same ts) |
| Trades closed today | **0** | Last close: **2026-07-30 00:49** |
| Trades opened (1h / 24h) | **0 / 0** | |
| Trades closed (1h / 24h) | **0 / 0** | |
| OPEN positions now | **0** | |
| Trades/hour (S42) | **0** | Paper book idle |
| Signals/hour (S40) | **~400** | S40 still writing (`max created` ~01:00 today) |
| Signals today / 24h | **400 / 1720** | Alive |
| G31 candidates today / 1h / 24h | **700 / ~620 / ~8480** | Alive; **100% rejected today** |
| S55 feature rows today / 1h | **100 / 100** | Mostly **REGIME_BLOCK**, `paper_trade_id=NULL` |
| Experiments/hour | **0** | Last research experiments: **2026-07-29** (20× WEAK) |
| Framework experiments (V1) | offline artifact | `EXPERIMENT_LEADERBOARD.md` at 2026-07-30 21:25 — not a live loop |
| ML samples (training CSV) | **50** | `research/ml/datasets/v1/training_dataset.csv` |
| Feature Store size | **~65 KB / 3 files** | `research/ml/feature_store/v1/` (mtime 2026-07-30 21:10) |
| ML model dir | **~100 KB / 4 files** | last train **2026-07-30 21:10** — **not updating continuously** |
| Optimizer runs (live loop) | **manual / stale** | `OPTIMIZER_REPORT.md` 2026-07-30 20:59 |
| Validation runs | **manual / stale** | `VALIDATION_REPORT.md` 2026-07-30 20:59 |
| Paper equity | **−1164.7** (initial 100) | Account last updated at last close |

### Workers (log mtimes ≈ now)

Alive: `me-shock-paper-core`, `me-learning-worker`, `me-ai-worker`, collectors.

Shock-paper heartbeat (recent):

- `paper_runs_open=0`
- `events_detected=0`
- shadow accept_rate **0.04%**
- detectors mostly `below_return_threshold`

### Why no new paper trades?

1. **S55 gate today:** fresh rows are almost all `REGIME_BLOCK` (RANGE regime).
2. **Historical S42:** 45/50 were `INSUFFICIENT_HISTORY`, only 5 `REGIME_EXPLORE`.
3. **G31 today:** 700/700 `rejected` (top: “No reversal confirmation”, weak volume, confidence &lt; 7.5).
4. SQLite **database is locked** storms in shock-paper / learning / event-engine.

### Errors / Warnings

| Source | Signal |
|---|---|
| `me-shock-paper-core.log` | Very high error volume historically; recent: **`database is locked`**, intermittent Bybit SSL ticker failures |
| `me-learning-worker.log` | Repeated **`sqlite3.OperationalError: database is locked`** |
| `me-g3-live.log` / `event-intelligence.log` | Same lock errors |
| `me-sqlite-write-trace.jsonl` | Last 5k sampled lines all mention lock contention |
| Warnings | Lock contention is the dominant operational warning; SSL fetch failures secondary |

**Stage 1 conclusion:** infrastructure heartbeats are green, but the **paper trading path is effectively frozen**. Features for blocked attempts still write; models/optimizer are **not** on a live refresh cadence.

---

## Stage 2 — Data quality

### Trade counts

| Window | S42 trades |
|---|---|
| All time | **50** (all CLOSED) |
| Today | **0** |
| Last hour | **0** |
| Last 24h | **0** |

This is a **tiny** book. Any PF/EV slice below is exploratory, not statistical proof.

### By symbol (closed S42)

| Symbol | N | PF | EV (pnl_usd) | WR |
|---|---:|---:|---:|---:|
| BTC | 3 | ∞ | +37.5 | 100% |
| ETH | 3 | ∞ | +118.8 | 100% |
| LINK | 3 | ∞ | +101.0 | 100% |
| ADA | 3 | ∞ | +1.7 | 67% |
| APT | 3 | 1.33 | +2.8 | 67% |
| SOL | 2 | 0.21 | −19.6 | 50% |
| SUI | 2 | 0.00 | −77.2 | 0% |
| ARB | 3 | 0.00 | −215.2 | 0% |
| NEAR | 2 | 0.00 | −234.4 | 0% |
| OP | 2 | 0.00 | −179.8 | 0% |
| … | … | … | … | … |

Winners cluster on a few names; losers on others — **n=2–3 per coin**, not actionable.

### By time of day (local UTC+3, close time)

| Bucket | N | PF | EV | WR |
|---|---:|---:|---:|---:|
| morning | 18 | **2.59** | +21.7 | 44% |
| night | 5 | 1.06 | +2.5 | 60% |
| afternoon | 0 | — | — | — |
| evening | 27 | **0.06** | −61.8 | 26% |

Evening mass of losers dominates. Possible clock confound (when batch closed), not proven TOD alpha.

### By weekday

| Day | N | PF | EV | WR |
|---|---:|---:|---:|---:|
| Monday | 24 | 0.06 | −69.5 | 29% |
| Tuesday | 21 | 2.59 | +18.6 | 38% |
| Thursday | 5 | 1.06 | +2.5 | 60% |

Same caveat: calendar correlates with the single batch lifecycle.

### By market regime

| Regime | N | Share |
|---|---:|---:|
| RANGE | **50 / 50** | **100%** |

No trend / volatile / quiet labels in the closed book. Cannot validate regime edge.

### By confidence

| Value | N |
|---|---:|
| **NULL** | **50 / 50 (100%)** |

**No distribution.** Cannot slice 0.50 / 0.55 / 0.60 / …  
S42 `decision_confidence` is always NULL; S55 JSON `decision_confidence` also NULL.

### By gate (closed trades)

| Gate | N | PF | EV | WR |
|---|---:|---:|---:|---:|
| INSUFFICIENT_HISTORY | 45 | 0.37 | −28.4 | 33% |
| REGIME_EXPLORE | 5 | 1.06 | +2.5 | 60% |
| ALLOWED | 0 | — | — | — |
| EV / EXPLORE (named) | 0 | — | — | — |

Almost everything that traded was “insufficient history”, not a clean ALLOWED path.

### PnL / MFE / MAE / holding

| Stat | Value |
|---|---|
| PnL mean | **−25.29** |
| PnL median | −11.58 |
| PnL min / max | −234.5 / +126.2 |
| Win / Loss / BE | 18 / 27 / 5 |
| MFE mean | +1.94% |
| MAE mean | −2.25% |
| Holding mean / median | 37183s (~10.3h) / 32655s |
| Exit reasons | TRAILING 26, STOP 12, STALE 8, PORTFOLIO_REPLACE 4 |

### Source / pattern / news

| Dimension | Observation |
|---|---|
| Signal source | **100% `validation_signal`** |
| Direction | **100% LONG** |
| News category | **100% none** |
| Pattern | effectively one provisional candidate blob — **no pattern diversity** |

**Stage 2 conclusion:** dataset is small, homogeneous, and missing the fields needed for real segmentation (confidence, regime variety, news, patterns).

---

## Stage 3 — Feature quality (critical)

### S55 column NULL rates (n=150)

| Feature | NULL % | Comment |
|---|---:|---|
| RSI | **100%** | column + JSON value empty |
| news_score | **100%** | |
| macro_score | **100%** | |
| ai_score | **100%** | |
| spread | **100%** | |
| etf_flow | **100%** | |
| decision_confidence | **100%** | |
| shock_score | **100%** | |
| ATR | 0% | but **constant 50.0 for all 150** |
| volatility | 0% | **constant 50.0** |
| funding | 0% | **constant 54.1** |
| fear_greed | 0% | **constant 22.0** |
| trend | 0% | **constant 0.0** |
| volume | 0% | **constant 0.0** |
| oi_delta | 0% | filled, but often identical across symbols at same ts |
| market_regime | 0% | always **RANGE** |

### ML Feature Store / training CSV (n=50)

| Feature | NULL % |
|---|---:|
| rsi | **100%** |
| ema20_distance / ema50 / ema200 | **100%** |
| vwap_distance | **100%** |
| news_score | **100%** |
| confidence | **100%** |
| funding / oi_delta | 0% (same placeholder-ish pipeline) |
| pattern | present as string, not informative diversity |

EMA / VWAP distances are **absent** from S55 JSON keys as usable distances (only `ema_trend=0.0` constant).

### Implication for ML

If ~70%+ of the intended predictive features are NULL **or** constant placeholders, **shadow ML ranking on this store is not learning market structure** — it is fitting noise / symbol idiosyncrasy on 50 rows.

**Stage 3 conclusion:** feature pipeline is broken or stubbed for the signals that matter (RSI, EMA, VWAP, news, AI, confidence). Fix features before more model training.

---

## Stage 4 — Is there an edge?

### All closed trades

| Metric | Value |
|---|---|
| N | 50 |
| PF | **0.438** |
| Expectancy (mean pnl_usd) | **−25.29** |
| Winrate | **36%** |

### By confidence

**Impossible** — 100% NULL. No 0.50 / 0.55 / 0.60 curve.

### By symbol / regime / TOD / weekday / source / pattern / news

See Stage 2 tables.

- **Source / news / pattern / regime / direction:** single bucket only → no comparative edge.
- **TOD / weekday:** morning/Tuesday look better, evening/Monday worse — **confounded**, n small.
- **Gate:** `REGIME_EXPLORE` (n=5) PF≈1.06 vs `INSUFFICIENT_HISTORY` (n=45) PF≈0.37 — hint only, not validated.
- **Symbols:** BTC/ETH/LINK look good, ARB/NEAR/OP/SUI awful — **n=2–3**, unstable.

### Edge verdict

> **No slice currently demonstrates a stable, sample-backed informational advantage.**

The honest research conclusion:

1. Problem is **not** “missing optimizer / ML / experiment framework”.
2. Problem is **upstream**: paper path stalled, gates blocking, features empty/constant, confidence missing, tiny homogeneous validation batch with negative expectancy.
3. Until new trades open with **real feature vectors + confidence**, optimizer/validation/ML will keep rediscovering the same weak sample.

---

## Recommended next checks (ops, not code yet)

1. **Unblock paper path** — why perpetual `REGIME_BLOCK` / no ALLOWED; confirm S57 regime + gate policy intentionally halt entries.
2. **SQLite lock contention** — workers fighting one DB; primary ops risk.
3. **Feature backfill / producers** — RSI, EMA distances, VWAP, news, AI, confidence must be non-null and non-constant on entry.
4. **Do not retrain ML** until Feature Store null/constant audit passes a minimum fill threshold (e.g. &lt;20% NULL on core tech features).
5. **Grow sample** — need hundreds of closed trades with varied regimes before trusting PF slices.

---

## Appendix — artifact freshness

| Artifact | Last update (UTC+3) |
|---|---|
| S42 last open | 2026-07-27 21:12 |
| S42 last close | 2026-07-30 00:49 |
| S55 latest row | ~2026-07-31 01:07 (blocks) |
| Feature Store / ML train | 2026-07-30 21:10 |
| Optimizer / Validation reports | 2026-07-30 20:59 |
| Experiment leaderboard | 2026-07-30 21:25 |
| Research experiments table | 2026-07-29 |

---

*Read-only diagnostic. No production strategy changes were made while generating this report.*
