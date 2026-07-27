# PROJECT_OS — Architecture

**Updated:** 2026-07-26  
**Rule:** Every research module / CLI / report **must declare its universe**.

---

## Universes (mandatory)

| Universe ID | Name | Primary store | Use |
|-------------|------|---------------|-----|
| `futures_paper` | Futures Paper Trading | LIVE S42 + RESEARCH S56 (paper closes) | **Default product analytics** |
| `polymarket_hist` | Polymarket hist / ER–shadow | RESEARCH S56 `hist:*` from `trades.db` | **Archived** research only |
| `mixed_unlabeled` | *(forbidden)* | any join without filter | Do not ship |

### Declaration requirement

New or touched research code should state universe in at least one of:

- module docstring (`Universe: futures_paper`)
- CLI banner / report header (`universe=futures_paper`)
- dataset `metadata.json` (`universe` field)
- `PROJECT_OS/DECISIONS.md` entry if introducing a new universe ID

---

## Two product stacks in one repo

```
┌─────────────────────────────────────────────────────────────┐
│ A. FUTURES PAPER (PRIMARY)                                  │
│    market_events: G3/Telegram/G4 → S40 → S42 paper          │
│    features S55 · regime S57 · decisions S58                │
│    analytics S56 (research) → S59–S66                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ B. POLYMARKET HIST (ARCHIVED RESEARCH)                      │
│    trades.db ER/shadow/virtual → backfill-history → S56     │
│    local n≈1786 · not LIVE paper journal                    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ C. Other stacks (secondary)                                 │
│    Polymarket live bot (bot/main) · futures_agent Telegram  │
│    Terminal V7 · optimizer/scientist (older Polymarket)     │
└─────────────────────────────────────────────────────────────┘
```

Detail inventories: `docs/ARCHITECTURE_AUDIT.md`, `docs/TRADE_DATA_FLOW.md`.

---

## Futures paper spine (do not redesign lightly)

```text
S40  signal ingest                 LIVE
  ↓
S42  paper open / tick / close     LIVE  ← operational journal (~28k target)
  ↓
S55  features at open (+ close)    LIVE
S57  regime attach/gate            LIVE
S58  decision trace                RESEARCH
  ↓
S60  write_close_analytics bridge
  ↓
S56  closed snapshots              RESEARCH  ← analytics warehouse
  ↓
S59–S66  labs / reports            RESEARCH
```

**Also:** LIVE migrations stop earlier than research (v65 vs v66–72 policy) — keep analytics DDL off the trading DB.

---

## Data planes

| Plane | Typical path | Owns |
|-------|--------------|------|
| LIVE market_events | `data/market_events.db` | S40, S42, S55, collectors |
| RESEARCH market_events | `data/market_events_research.db` | S56–S66, S58 |
| Polymarket trades | `data/trades.db` | ER/shadow hist **sources** (archive input) |
| Lab export | `research/datasets/` | Canonical open-time features (**must tag universe**) |

---

## Research export layer (Phase 5B+)

| Artifact | Intent |
|----------|--------|
| `research_dataset_builder.py` | Open-time-only rows; reject leakage |
| `dataset_validator.py` | Quality gates before ML |
| `dataset_provenance_audit.py` | Count / source reconciliation |

**Future rule:** builder/validator/provenance runs must set `universe=` (`futures_paper` or `polymarket_hist`). Current local parquet was built from **polymarket_hist** — treat as archive artifact until rebuilt on futures paper.

---

## Shared libs (safe consolidation)

Under `bot/research/market_events/signal_intelligence/lib/`:

- `feature_utils.py` — session, coin, score scale, safe_float (Phase 4A)

Planned (consolidation plan): `metrics.py`, `trade_stats.py`, `ranking.py`, `report_io.py`, `snapshot.py` — behavior-preserving only.

---

## CLI / supervisor

- ~194 `market_events` CLI commands; ~12 supervisor workers via `start-all`.
- Paper ops: `learning-worker`, `paper-performance` (LIVE).
- Analytics: `pnl-killers`, `morning-report`, `audit-trade-data`, etc. (RESEARCH / `load_lab_trades` today — **universe often implicit**; fix when migrating).

---

## What must not be conflated

| Mistake | Why it hurts |
|---------|----------------|
| Reporting hist PnL as futures paper edge | Wrong product |
| Using `paper_trade_id` alone across `hist:*` families | ID collision across source tables |
| Feeding exit/MFE/`btc_move_*` into entry filters | Leakage |
| Dropping LIVE/RESEARCH split “for simplicity” | Lock & corruption risk |
