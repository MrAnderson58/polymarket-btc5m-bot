# Refactor Phase 4A — Extract `feature_utils.py`

**Date:** 2026-07-25  
**Commit intent:** behavior-preserving extract of duplicated pure helpers  
**Constraint:** no architecture / algorithm / DB / CLI / report-format changes

---

## Goal

Move identical pure helpers into one shared module and replace call-site copies with imports.

## New location

`bot/research/market_events/signal_intelligence/lib/feature_utils.py`  
(package marker: `lib/__init__.py`)

## Functions extracted

| Function | Role |
|----------|------|
| `safe_float(v)` | Feature value → `float` or `None` |
| `session_from_hour(hour)` | UTC hour → Asia / London / NewYork / Offhours |
| `normalize_coin(raw)` | Symbol → upper, strip `USDT`, strip whitespace |
| `normalize_score_0_100(v)` | Map unit-interval scores (`0–1`) to `0–100`; leave other scales unchanged |

### Intentionally not extracted (not identical duplicates)

| Candidate | Why left alone |
|-----------|----------------|
| S56 inline session (`else → Offhours`) | Differs from S55/S66 on invalid hours (`None` vs `Offhours`) |
| S621 / S56 coin without `.strip()` | Strip vs no-strip would change edge outputs |
| Funding bucket label / `_bucket_name` | S64 returns `"unknown"` for miss; S623 returns `None` |
| BTC dominance helper | No duplicated pure helper beyond `safe_float` of a field |
| `normalize_symbol` (S50) | Adds regex validation — not the same as `normalize_coin` |

---

## Files changed

### Added

- `bot/research/market_events/signal_intelligence/lib/__init__.py`
- `bot/research/market_events/signal_intelligence/lib/feature_utils.py`
- `docs/REFACTOR_PHASE4A.md` (this file)

### Migrated (local copy → import)

| File | Replaced |
|------|----------|
| `trade_intelligence_s55.py` | `_safe_float`, `_session_from_hour` |
| `trade_data_audit_s66.py` | `_session_from_hour`; coin extractor → `normalize_coin` |
| `trading_intelligence_report_s621.py` | `_safe_float`; AI bucket value → `normalize_score_0_100` |
| `pnl_killers_s64.py` | `_ai_scaled` / `_confidence_scaled` scale step; `_coin` → `normalize_coin` |
| `drift_analyzer_s622.py` | `_ai_value` → `normalize_score_0_100` |
| `pattern_discovery_s623.py` | `_ai_scaled`; `tag_trade` coin → `normalize_coin` |
| `trade_postmortem_s56.py` | `_safe_float` only (session/coin left as-is) |
| `decision_trace_s58.py` | `_safe_float` |
| `signal_paper_performance_s42.py` | `_safe_float` |
| `feature_lab_s59.py` | `_safe_float` |
| `market_regime_s57.py` | `_safe_float` |

Old locations of the shared bodies were the local `def` blocks listed above (removed in favor of imports aliased as `_safe_float` / `_session_from_hour` where call sites already used those names).

---

## Verification performed

### 1. Pre-migration fixtures

Captured outputs of the old helpers into `/tmp/phase4a_fixtures/old_helpers.json` for:

- `session_from_hour` (S55 + S66)
- `safe_float` (S55 + S621)
- AI scale (`_ai_scaled` S64/S623, `_ai_value` S622, S621 bucket fn)
- `_confidence_scaled` (S64)
- `_coin` (S64) and strip-style coin normalize

Confirmed pre-migration duplicates already agreed with each other.

### 2. Post-migration helper compare

Re-ran the same fixtures through migrated wrappers and `lib.feature_utils`.

**Result:** JSON-serialized outputs **byte-for-byte identical** to the pre-migration fixture file.

### 3. CLI report compare

Ran before and after:

- `paper-performance --today`
- `audit-trade-data`
- `pnl-killers`
- `long-short-analysis`
- `morning-report`

Compared written JSON/Markdown under `research/reports/…` and `paper-performance` stdout.

**Result:** Feature-relevant payloads identical after normalizing wall-clock / host-volatile fields only:

- `elapsed_sec`, `generated_at`, `generated_at_iso`
- disk usage figures, telegram `poll_uptime`
- morning patterns `_source=computed` vs `cache` (second run reads cache)

No metric / segment / fill-rate differences attributed to the helper extract.

---

## No behavior changes confirmed

- Helper fixture equality: **PASS**
- PnL killers / long-short / audit JSON+MD (elapsed-normalized): **PASS**
- Morning core payload (excluding host/cache volatility): **PASS**
- Paper-performance stdout (timestamp-normalized): **PASS**

---

*End of Phase 4A evidence note.*
