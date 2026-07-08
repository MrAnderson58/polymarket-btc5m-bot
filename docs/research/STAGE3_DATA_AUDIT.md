# Futures Agent Stage 3 — Production Data Audit

**Status:** COMPLETE — live production audit executed 2026-07-08 (Mac Mini / `trading_ai`).

---

## Live production findings (2026-07-08)

### `telegram_messages` (primary corpus)

| Metric | Value |
|--------|-------|
| Total rows | 370,876 |
| Channels | 3 |
| `lookonchain` | 351,573 (~95%) |
| `WatcherGuru` | 11,773 |
| `signalyp` | 7,530 |
| Timestamp range | 2021-08-23 → 2026-06-15 |

**Columns:** `id`, `channel_name`, `message_text`, `message_date`, `collected_at`, `telegram_message_id`

**Missing:** author, reply, forward metadata

### Legacy taxonomy sample (recent 25k)

| Type | Count |
|------|-------|
| OTHER | 12,443 |
| NEWS | 5,966 |
| EXPLICIT_SIGNAL | 2,783 |
| MARKET_COMMENTARY | 1,326 |
| PROMO | 1,273 |
| TRADE_UPDATE | 685 |
| MARKET_REVIEW | 214 |
| TP_HIT | 187 |
| POSITION_CLOSE | 122 |
| SL_HIT | 1 |

**Audit conclusion:** Legacy taxonomy is semantically insufficient for Stage 3. Whale/on-chain
observations are misclassified as `EXPLICIT_SIGNAL`; valuable `signalyp` commentary falls into `OTHER`.
Stage 3 uses a **separate research taxonomy** (`research_taxonomy.py`) — production classifier unchanged.

### Other tables

| Table | Status |
|-------|--------|
| `news` | 20 rows — `published_at`, `summary`, `symbols`, `sentiment` 100% null → **unusable** |
| `source_ratings` | empty |
| `telegram_channels` | empty |
| `telegram_signals` | empty |

**Historical news corpus for Stage 3:** `NEWS_EVENT` posts from `telegram_messages` only.

---

## Phase B commands (Mac Mini)

```bash
cd ~/polymarket-btc5m-bot && source .venv/bin/activate

python -m bot.research.futures_agent stage3-migrate
python -m bot.research.futures_agent research-classify-audit --sample-size 500
python -m bot.research.futures_agent ingest-research --source-table telegram_messages
python -m bot.research.futures_agent thesis-extract
python -m bot.research.futures_agent research-stats
```

Optional filters: `--channel`, `--start-ts`, `--end-ts`, `--limit`, `--max-per-source`

**Expected full ingest runtime:** ~15–45 min for 370k rows (chunked classify-only, no Binance).

---

## How to run the live audit (re-run)

On Mac Mini (where `telegram_messages` lives):

```bash
cd ~/polymarket-btc5m-bot
source .venv/bin/activate
# Ensure .env contains:
#   FUTURES_SOURCE_DATABASE_URL=postgresql:///trading_ai
#   FUTURES_SOURCE_BACKEND=postgres
#   FUTURES_REQUIRE_POSTGRES=true

python -m bot.research.futures_agent stage3-audit
# Writes docs/research/STAGE3_DATA_AUDIT.md and prints summary

# Optional raw JSON:
python -m bot.research.futures_agent stage3-audit --json data/stage3_audit_raw.json
```

The audit is **read-only**. It never INSERT/UPDATE/DELETE on source tables.

---

## Audit scope

| Table | Purpose |
|-------|---------|
| `telegram_messages` | Primary historical Telegram corpus (~370k rows cited in architecture audit) |
| `news` | Project news DB — usability unknown until live audit |
| `source_ratings` | Precomputed source scores — usability unknown until live audit |
| `telegram_channels` | Channel metadata |
| `telegram_signals` | Alternate/legacy signal table if present |

---

## Confirmed schema: `telegram_messages` (from production integration tests)

Validated in `tests/test_futures_production_schema.py` against the production column layout:

| Column | Type (test fixture) | Notes |
|--------|---------------------|-------|
| `id` | BIGINT | Internal row id |
| `channel_name` | TEXT | Maps to `source` in `RawMessage` |
| `message_text` | TEXT | Primary body; maps to `text` |
| `message_date` | TIMESTAMP | Publication time; maps to `timestamp` |
| `collected_at` | TIMESTAMP | Ingestion time |
| `telegram_message_id` | BIGINT | External Telegram message id |

**Column mapping** (`source_data.py`): `message_text` → text, `message_date` → ts,
`telegram_message_id` → message_id, `channel_name` → source.

### Not present in confirmed production schema

The following are **not** in the test-validated `telegram_messages` layout:

- `author` / `sender` / `from_user`
- `reply_to_message_id`
- `forward_from` / `forward_origin`
- `raw_json` / `metadata_json`

**Implication:** Original author for forwarded posts is likely embedded in `message_text`
(e.g. "Forwarded from …") or lost unless the external collector stores extra columns.
Live audit will confirm actual production columns via `information_schema`.

### Forwarded message handling (live inbound only)

`futures_agent_inputs.status_detail` stores JSON for live Telegram forwards:

```json
{
  "telegram_chat_id": 123,
  "telegram_message_id": 456,
  "forward_origin": { ... }
}
```

Historical `telegram_messages` rows may not have structured forward metadata.

---

## Tables referenced but not defined in this repository

`source_data.SOURCE_TABLES` lists:

- `telegram_channels`
- `telegram_messages` ✅ (schema partially confirmed)
- `telegram_signals`
- `market_prices`
- `news`
- `ai_signals`
- `source_ratings`

**No DDL, reader, or sample rows** for `news` or `source_ratings` exist in-repo.
Stage 3 MVP must treat them as **audit-dependent**:
- If `news` has recent rows with timestamps + symbols → include in EvidencePacket Phase F
- If empty/stale → skip news block; deterministic gate `news_risk` component = 0

---

## Taxonomy / relevance filter methodology

Classifier: `bot/research/futures/taxonomy.py` (`classify_message`).

### Message types

| Type | Stage 3 research relevance |
|------|---------------------------|
| `EXPLICIT_SIGNAL` | Existing signal path + optional thesis |
| `MARKET_REVIEW` | **Primary research ingestion target** |
| `MARKET_COMMENTARY` | **Primary research ingestion target** |
| `NEWS` | Ingest only if directional/market claims extractable |
| `TRADE_UPDATE`, `TP_HIT`, `SL_HIT`, `POSITION_CLOSE` | Lifecycle — thesis state updates, not new entries |
| `PROMO` | Skip |
| `OTHER` | Skip unless deterministic extraction confident |

### Current live pipeline behavior (unchanged)

`pipeline.py` sets `STATUS_REJECTED` for `MARKET_REVIEW`, `MARKET_COMMENTARY`, `NEWS`.
Stage 3 adds a **parallel fork** after taxonomy — it does **not** modify explicit-signal gate.

### Estimated survival rates (pending live audit)

The audit script classifies a sample of recent `telegram_messages` and extrapolates:

```
research_like_pct = (MARKET_REVIEW + MARKET_COMMENTARY + NEWS) / sample_size
extrapolated_research_rows ≈ total_rows * research_like_pct
```

**Placeholder until Mac Mini run** — prior architecture doc cited ~370k total messages.
If research-like rate is 15–25% (typical for mixed signal+analysis channels), expect
**~55k–92k** candidate research posts before extraction confidence filtering.

After deterministic thesis extraction (confidence ≥ 0.6), expect **30–50%** of research-like
posts to yield ≥1 thesis → **~17k–46k** theses (order-of-magnitude; replace with audit output).

---

## Duplicate / repost characteristics (to measure on live audit)

Audit script reports on 25k recent message sample:

- `forward_hint_rate` — text contains "forwarded from" / "переслан"
- `repost_hint_rate` — `t.me/` link in first 200 chars
- `dup_text_rate` — normalized text collision within sample
- `duplicate_msg_id_channel_groups` — same `(telegram_message_id, channel_name)` repeated

Dedup policy for Stage 3 MVP: `content_hash = sha256(normalize(text))` per source;
skip insert if same hash seen within 7 days.

---

## Live audit output sections (auto-generated on Mac Mini)

When `stage3-audit` succeeds, this file is overwritten with:

1. Exact `information_schema` columns for all five tables
2. Row counts and timestamp ranges
3. Null rates for important fields
4. Top 15 channels by volume
5. Taxonomy distribution (25k recent + 5k stratified)
6. Example previews for REVIEW / COMMENTARY / NEWS / EXPLICIT_SIGNAL
7. Sample rows for `news`, `source_ratings`, `telegram_channels`
8. Usability verdict for news and source_ratings tables

---

## Usability verdict template (fill after live audit)

### `news`

- [ ] Table exists
- [ ] Row count > 0
- [ ] Has usable timestamp column
- [ ] Has headline/body text
- [ ] Symbol tagging present
- [ ] Data fresh (latest row within 7 days)
- **Verdict:** TBD

### `source_ratings`

- [ ] Table exists
- [ ] Row count > 0
- [ ] Maps to `channel_name` / `source` in telegram_messages
- [ ] Scores numerically meaningful
- **Verdict:** TBD — if empty, compute scores from `futures_agent_thesis_outcomes` only

---

## Related existing data stores

| Store | Location | Stage 3 use |
|-------|----------|-------------|
| Agent PG/SQLite | `futures_agent_*` | Write target for theses, scores, evidence |
| Research SQLite | `futures_research_*` in `trades.db` | Batch outcomes; port logic to agent |
| Live inputs | `futures_agent_inputs` | Forward metadata for inbound only |

**No sync** exists between research SQLite and agent PG today.
