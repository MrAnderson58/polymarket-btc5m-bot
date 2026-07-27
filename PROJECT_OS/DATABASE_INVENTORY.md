# PROJECT_OS — Database Inventory

_Read-only audit of `data/*.db`. Generated 2026-07-25T22:37:11.843731+00:00. No data modified._

## Scope

All SQLite files under `data/`.

| Database | Size | Tables | Role |
|----------|-----:|-------:|------|
| `data/futures_agent.db` | 0.25 MB | 19 | Futures Telegram agent research DB (mostly empty locally) |
| `data/market_events.db` | 150.52 MB | 148 | LIVE market-events DB — collectors, G3, S40/S42/S55 ops (futures paper spine) |
| `data/market_events_research.db` | 3.91 MB | 15 | RESEARCH sibling — S56–S62 analytics (local: 1786 hist snapshots) |
| `data/trades.db` | 73.95 MB | 57 | Polymarket bot primary DB — ER/shadow trades, features, learning stacks |
| `data/trades_before_live.db` | 22.75 MB | 8 | Snapshot backup of trades.db (pre-live) |
| `data/trades_before_no_c_yes_b.db` | 13.57 MB | 7 | Snapshot backup (pre NO_C/YES_B change) |
| `data/trades_before_only_no_c.db` | 16.29 MB | 7 | Snapshot backup (only NO_C era) |
| `data/trades_before_yesc_20260628_2145.db` | 39.70 MB | 13 | Snapshot backup (pre YES_C 2026-06-28) |

## Recovery finding (paper trades)

On **this machine**:

- Futures paper journal `market_events_paper_trades_s42` = **0** rows (LIVE).
- Research analytics `market_events_trade_snapshots_s56` = **1786** rows (all `hist:*` Polymarket backfill).
- Polymarket `trades.db` closed/shadow trade tables sum to **~1.8k** strategy trades (not ~28k).
- **No SQLite under `data/` currently holds ~28 000 futures paper trades.**
- The ~28k figure in `PROJECT_OS` is the **target / production reference** universe (`futures_paper`); it is **not present in this workspace’s local DBs**.

Backup DBs `trades_before_*.db` are historical Polymarket snapshots, not futures paper.

## Category indexes

### Paper / shadow / virtual trade tables

| Database | Table | Rows |
|----------|-------|-----:|
| `data/trades.db` | `early_reversion_markets` | 831 |
| `data/trades_before_live.db` | `early_reversion_markets` | 831 |
| `data/trades_before_yesc_20260628_2145.db` | `early_reversion_markets` | 831 |
| `data/trades.db` | `early_reversion_v2_trades` | 513 |
| `data/trades.db` | `early_reversion_trades` | 497 |
| `data/trades_before_live.db` | `early_reversion_trades` | 497 |
| `data/trades_before_yesc_20260628_2145.db` | `early_reversion_trades` | 497 |
| `data/trades_before_only_no_c.db` | `early_reversion_markets` | 486 |
| `data/trades_before_yesc_20260628_2145.db` | `early_reversion_v2_trades` | 445 |
| `data/trades_before_only_no_c.db` | `early_reversion_trades` | 439 |
| `data/trades_before_live.db` | `early_reversion_v2_trades` | 410 |
| `data/trades_before_only_no_c.db` | `early_reversion_v2_trades` | 365 |
| `data/trades_before_no_c_yes_b.db` | `early_reversion_markets` | 350 |
| `data/trades_before_no_c_yes_b.db` | `early_reversion_trades` | 342 |
| `data/trades.db` | `early_reversion_v3_trades` | 282 |
| `data/trades_before_live.db` | `early_reversion_v3_trades` | 282 |
| `data/trades_before_yesc_20260628_2145.db` | `early_reversion_v3_trades` | 282 |
| `data/trades_before_no_c_yes_b.db` | `early_reversion_v2_trades` | 268 |
| `data/trades_before_only_no_c.db` | `early_reversion_v3_trades` | 230 |
| `data/trades.db` | `early_reversion_v25_trades` | 221 |
| `data/trades_before_live.db` | `early_reversion_v25_trades` | 221 |
| `data/trades_before_yesc_20260628_2145.db` | `early_reversion_v25_trades` | 221 |
| `data/trades_before_only_no_c.db` | `early_reversion_v25_trades` | 179 |
| `data/trades_before_no_c_yes_b.db` | `early_reversion_v3_trades` | 133 |
| `data/trades.db` | `yes_c_shadow_trades` | 124 |
| `data/trades.db` | `v4_shadow_trades` | 107 |
| `data/trades_before_no_c_yes_b.db` | `early_reversion_v25_trades` | 82 |
| `data/trades.db` | `virtual_trades` | 48 |
| `data/trades_before_live.db` | `virtual_trades` | 48 |
| `data/trades_before_yesc_20260628_2145.db` | `virtual_trades` | 48 |
| `data/trades_before_yesc_20260628_2145.db` | `v4_shadow_trades` | 37 |
| `data/trades_before_only_no_c.db` | `virtual_trades` | 29 |
| `data/trades_before_no_c_yes_b.db` | `virtual_trades` | 15 |
| `data/market_events.db` | `ai_paper_trades_s47` | 0 |
| `data/market_events.db` | `market_events_paper_trades_s42` | 0 |
| `data/trades.db` | `bidirectional_shadow_trades` | 0 |
| `data/trades.db` | `evolution_regime_shadow_trades` | 0 |

### Feature / snapshot tables

| Database | Table | Rows |
|----------|-------|-----:|
| `data/market_events.db` | `market_events_snapshot_history_g51` | 3800 |
| `data/market_events_research.db` | `market_events_trade_snapshots_s56` | 1786 |
| `data/market_events.db` | `market_snapshots_g3` | 949 |
| `data/trades.db` | `ai_features` | 513 |
| `data/trades.db` | `trade_features` | 513 |
| `data/market_events.db` | `market_event_snapshots` | 12 |
| `data/futures_agent.db` | `futures_agent_btc_context` | 0 |
| `data/futures_agent.db` | `futures_agent_market_snapshots` | 0 |
| `data/futures_agent.db` | `futures_agent_relative_strength` | 0 |
| `data/market_events.db` | `market_events_trade_features_s55` | 0 |
| `data/market_events.db` | `market_events_trade_snapshots_s56` | 0 |
| `data/market_events_research.db` | `market_events_feature_lab_runs_s59` | 0 |
| `data/market_events_research.db` | `market_events_feature_lab_s59` | 0 |
| `data/trades.db` | `futures_signal_snapshots` | 0 |
| `data/trades.db` | `multi_timeframe_snapshots` | 0 |

### Filter / threshold tables

| Database | Table | Rows |
|----------|-------|-----:|
| `data/trades.db` | `no_c_filter_shadow_entries` | 91 |
| `data/trades.db` | `no_c_filter_shadow_counters` | 12 |
| `data/trades.db` | `no_c_filter_live_counters` | 1 |
| `data/market_events.db` | `market_validation_optimizer_g4` | 0 |
| `data/market_events_research.db` | `market_events_regime_ops_s57` | 0 |

### Learning / evolution tables

| Database | Table | Rows |
|----------|-------|-----:|
| `data/trades.db` | `brain_memory` | 542 |
| `data/trades.db` | `brain_trade_context` | 513 |
| `data/trades.db` | `scientist_experiments` | 27 |
| `data/trades.db` | `scientist_hypotheses` | 27 |
| `data/trades.db` | `brain_knowledge` | 21 |
| `data/market_events.db` | `market_events_signal_learning_s40_ops_state` | 5 |
| `data/trades.db` | `brain_learning_state` | 3 |
| `data/trades.db` | `evolution_shadow` | 2 |
| `data/market_events.db` | `market_events_learning_daily_s43` | 1 |
| `data/market_events.db` | `market_events_g2_learning_notes` | 0 |
| `data/market_events.db` | `market_events_reversal_learning_g1` | 0 |
| `data/market_events.db` | `market_events_signal_learning_s40_checkpoints` | 0 |
| `data/market_events.db` | `market_events_signal_learning_s40_reviews` | 0 |
| `data/market_events.db` | `market_events_signal_learning_s40_signals` | 0 |
| `data/market_events.db` | `market_experimental_followup_g39` | 0 |
| `data/market_events.db` | `market_experimental_signals_g39` | 0 |
| `data/market_events.db` | `market_learning_dataset` | 0 |
| `data/trades.db` | `evolution_history` | 0 |
| `data/trades.db` | `evolution_observe_stats` | 0 |
| `data/trades.db` | `evolution_regime_shadow` | 0 |
| `data/trades.db` | `evolution_regime_shadow_trades` | 0 |
| `data/trades.db` | `evolution_shadow_evaluations` | 0 |
| `data/trades.db` | `scientist_patterns` | 0 |

### Event / market-event tables

| Database | Table | Rows |
|----------|-------|-----:|
| `data/market_events.db` | `market_events_near_miss_summaries` | 545102 |
| `data/trades.db` | `er_funnel_events` | 13420 |
| `data/market_events.db` | `market_events_historical_candles` | 6366 |
| `data/market_events.db` | `market_events_liquidity_history_g51` | 4000 |
| `data/market_events.db` | `market_events_snapshot_history_g51` | 3800 |
| `data/market_events.db` | `market_events_candle_patterns_g51` | 3600 |
| `data/market_events.db` | `market_events_replay_timeline_g51` | 3325 |
| `data/market_events.db` | `market_events_command_trace_g351` | 179 |
| `data/market_events.db` | `market_news_feed_n11` | 101 |
| `data/market_events.db` | `market_event_snapshots` | 12 |
| `data/market_events.db` | `market_events` | 12 |
| `data/market_events.db` | `market_events_signal_trace_f51` | 12 |
| `data/trades.db` | `er_health_events` | 10 |
| `data/market_events.db` | `market_intel_events` | 7 |
| `data/market_events.db` | `market_macro_events` | 7 |
| `data/market_events.db` | `market_signal_inbox_s23` | 7 |
| `data/market_events.db` | `market_event_telegram_delivery_log` | 3 |
| `data/market_events.db` | `market_events_universe_log` | 3 |
| `data/market_events.db` | `market_events_inbound_trace_g04` | 2 |
| `data/market_events.db` | `market_events_learning_daily_s43` | 1 |
| `data/market_events.db` | `market_events_paper_account_s42` | 1 |
| `data/market_events.db` | `market_events_paper_reports_s42` | 1 |
| `data/market_events.db` | `market_events_scheduler_state` | 1 |
| `data/futures_agent.db` | `futures_agent_research_signal_events` | 0 |
| `data/market_events.db` | `market_event_ai_analyses` | 0 |
| `data/market_events.db` | `market_event_ai_analyses_f0` | 0 |
| `data/market_events.db` | `market_event_alert_log` | 0 |
| `data/market_events.db` | `market_event_analysis_jobs` | 0 |
| `data/market_events.db` | `market_event_context` | 0 |
| `data/market_events.db` | `market_event_exchange_context` | 0 |
| `data/market_events.db` | `market_event_exchange_symbols` | 0 |
| `data/market_events.db` | `market_event_lifecycle_decisions` | 0 |
| `data/market_events.db` | `market_events_ai_comparisons` | 0 |
| `data/market_events.db` | `market_events_ai_research_g2` | 0 |
| `data/market_events.db` | `market_events_alert_rankings_f3` | 0 |
| `data/market_events.db` | `market_events_candle_backfill_checkpoints` | 0 |
| `data/market_events.db` | `market_events_claude_requests_s50` | 0 |
| `data/market_events.db` | `market_events_counterfactual_studies` | 0 |
| `data/market_events.db` | `market_events_digest_log` | 0 |
| `data/market_events.db` | `market_events_discovery_runs` | 0 |
| `data/market_events.db` | `market_events_entity_registry` | 0 |
| `data/market_events.db` | `market_events_entry_stages_f3` | 0 |
| `data/market_events.db` | `market_events_exhaustion` | 0 |
| `data/market_events.db` | `market_events_funding_oi_history_f2` | 0 |
| `data/market_events.db` | `market_events_g1_pattern_stats` | 0 |
| `data/market_events.db` | `market_events_g2_learning_notes` | 0 |
| `data/market_events.db` | `market_events_g2_prompt_cache` | 0 |
| `data/market_events.db` | `market_events_g2_visual_analysis` | 0 |
| `data/market_events.db` | `market_events_instruments` | 0 |
| `data/market_events.db` | `market_events_liquidity_trend_g1` | 0 |
| `data/market_events.db` | `market_events_market_intelligence_f7` | 0 |
| `data/market_events.db` | `market_events_mtf_paper_runs` | 0 |
| `data/market_events.db` | `market_events_mtf_replay_results` | 0 |
| `data/market_events.db` | `market_events_mtf_trend_g1` | 0 |
| `data/market_events.db` | `market_events_multitimeframe` | 0 |
| `data/market_events.db` | `market_events_near_miss_f73` | 0 |
| `data/market_events.db` | `market_events_opportunity_scores` | 0 |
| `data/market_events.db` | `market_events_opportunity_scores_v2` | 0 |
| `data/market_events.db` | `market_events_pattern_stats_f72` | 0 |
| `data/market_events.db` | `market_events_pending_shocks` | 0 |
| `data/market_events.db` | `market_events_postmortem_ops_s56` | 0 |
| `data/market_events.db` | `market_events_postmortem_runs_s56` | 0 |
| `data/market_events.db` | `market_events_price_observations` | 0 |
| `data/market_events.db` | `market_events_profile_shadow_candidates` | 0 |
| `data/market_events.db` | `market_events_quant_reports_g50` | 0 |
| `data/market_events.db` | `market_events_replay_ai_critic` | 0 |
| `data/market_events.db` | `market_events_replay_context_links` | 0 |
| `data/market_events.db` | `market_events_replay_path_metrics` | 0 |
| `data/market_events.db` | `market_events_replay_runs` | 0 |
| `data/market_events.db` | `market_events_replay_shocks` | 0 |
| `data/market_events.db` | `market_events_replay_splits` | 0 |
| `data/market_events.db` | `market_events_replay_strategy_results` | 0 |
| `data/market_events.db` | `market_events_research_artifacts_s50` | 0 |
| `data/market_events.db` | `market_events_reversal_learning_g1` | 0 |
| `data/market_events.db` | `market_events_rule_suggestions_s56` | 0 |
| `data/market_events.db` | `market_events_runner_state` | 0 |
| `data/market_events.db` | `market_events_shadow_candidates` | 0 |
| `data/market_events.db` | `market_events_signal_learning_s40_checkpoints` | 0 |
| `data/market_events.db` | `market_events_signal_learning_s40_reviews` | 0 |
| `data/market_events.db` | `market_events_signal_learning_s40_signals` | 0 |
| `data/market_events.db` | `market_events_signal_outcomes_f1` | 0 |
| `data/market_events.db` | `market_events_signal_outcomes_f72` | 0 |
| `data/market_events.db` | `market_events_signal_priority_f5` | 0 |
| `data/market_events.db` | `market_events_signal_ranking_weekly` | 0 |
| `data/market_events.db` | `market_events_signal_reports_f1` | 0 |
| `data/market_events.db` | `market_events_signal_reports_f2` | 0 |
| `data/market_events.db` | `market_events_signal_reports_f5` | 0 |
| `data/market_events.db` | `market_events_timeline_cache` | 0 |
| `data/market_events.db` | `market_events_trader_performance_f6` | 0 |
| `data/market_events.db` | `market_events_trend_shock` | 0 |
| `data/market_events.db` | `market_events_trend_shock_v2` | 0 |
| `data/market_events.db` | `market_events_visual_intel_f4` | 0 |
| `data/market_events_research.db` | `market_events_alpha_discovery_runs_s62` | 0 |
| `data/market_events_research.db` | `market_events_alpha_discovery_s62` | 0 |
| `data/market_events_research.db` | `market_events_feature_lab_runs_s59` | 0 |
| `data/market_events_research.db` | `market_events_feature_lab_s59` | 0 |
| `data/market_events_research.db` | `market_events_postmortem_ops_s56` | 0 |
| `data/market_events_research.db` | `market_events_postmortem_runs_s56` | 0 |
| `data/market_events_research.db` | `market_events_regime_ops_s57` | 0 |
| `data/market_events_research.db` | `market_events_regime_runs_s57` | 0 |
| `data/market_events_research.db` | `market_events_research_ops_s60` | 0 |
| `data/market_events_research.db` | `market_events_rule_suggestions_s56` | 0 |
| `data/market_events_research.db` | `market_events_strategy_discovery_runs_s61` | 0 |
| `data/market_events_research.db` | `market_events_strategy_discovery_s61` | 0 |
| `data/market_events_research.db` | `market_events_trade_decisions_s58` | 0 |

### Strategy result / validation / discovery tables

| Database | Table | Rows |
|----------|-------|-----:|
| `data/market_events.db` | `market_score_breakdown_g34` | 200214 |
| `data/market_events.db` | `market_candidate_g31` | 18920 |
| `data/market_events.db` | `market_candidate_outcomes_g32` | 18920 |
| `data/market_events.db` | `market_validation_daily_g4` | 1 |
| `data/market_events.db` | `market_validation_signals` | 1 |
| `data/futures_agent.db` | `futures_agent_research_signal_outcomes` | 0 |
| `data/futures_agent.db` | `futures_agent_thesis_outcomes` | 0 |
| `data/market_events.db` | `ai_signal_outcomes_s48` | 0 |
| `data/market_events.db` | `market_events_discovery_runs` | 0 |
| `data/market_events.db` | `market_events_g1_pattern_stats` | 0 |
| `data/market_events.db` | `market_events_pattern_stats_f72` | 0 |
| `data/market_events.db` | `market_events_postmortem_ops_s56` | 0 |
| `data/market_events.db` | `market_events_postmortem_runs_s56` | 0 |
| `data/market_events.db` | `market_events_profile_shadow_candidates` | 0 |
| `data/market_events.db` | `market_events_replay_strategy_results` | 0 |
| `data/market_events.db` | `market_events_rule_suggestions_s56` | 0 |
| `data/market_events.db` | `market_events_shadow_candidates` | 0 |
| `data/market_events.db` | `market_events_signal_outcomes_f1` | 0 |
| `data/market_events.db` | `market_events_signal_outcomes_f72` | 0 |
| `data/market_events.db` | `market_g35_candidate_state` | 0 |
| `data/market_events.db` | `market_validation_analysis_g4` | 0 |
| `data/market_events.db` | `market_validation_factor_stats_g4` | 0 |
| `data/market_events.db` | `market_validation_horizons` | 0 |
| `data/market_events.db` | `market_validation_optimizer_g4` | 0 |
| `data/market_events.db` | `market_validation_recommendations_g4` | 0 |
| `data/market_events.db` | `market_validation_records_g4` | 0 |
| `data/market_events.db` | `market_validation_symbol_stats_g4` | 0 |
| `data/market_events_research.db` | `market_events_alpha_discovery_runs_s62` | 0 |
| `data/market_events_research.db` | `market_events_alpha_discovery_s62` | 0 |
| `data/market_events_research.db` | `market_events_postmortem_ops_s56` | 0 |
| `data/market_events_research.db` | `market_events_postmortem_runs_s56` | 0 |
| `data/market_events_research.db` | `market_events_rule_suggestions_s56` | 0 |
| `data/market_events_research.db` | `market_events_strategy_discovery_runs_s61` | 0 |
| `data/market_events_research.db` | `market_events_strategy_discovery_s61` | 0 |
| `data/trades.db` | `futures_signal_outcomes` | 0 |
| `data/trades.db` | `ss_shadow_candidates` | 0 |
| `data/trades.db` | `ss_simulation_results` | 0 |

### Report / digest tables

| Database | Table | Rows |
|----------|-------|-----:|
| `data/market_events.db` | `market_daily_briefs` | 3 |
| `data/market_events.db` | `market_daily_report_g3` | 1 |
| `data/market_events.db` | `market_events_paper_reports_s42` | 1 |
| `data/market_events.db` | `market_events_digest_log` | 0 |
| `data/market_events.db` | `market_events_quant_reports_g50` | 0 |
| `data/market_events.db` | `market_events_signal_reports_f1` | 0 |
| `data/market_events.db` | `market_events_signal_reports_f2` | 0 |
| `data/market_events.db` | `market_events_signal_reports_f5` | 0 |

## Full inventory

Columns: **Database · Table · Rows · Purpose · Producer · Consumer · Categories**

### `data/futures_agent.db`

Futures Telegram agent research DB (mostly empty locally)

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `futures_agent_btc_context` | 0 | Feature / snapshot / lab feature storage | `futures_agent regime/context` | `context_report` | feature |
| `futures_agent_inputs` | 0 | Raw/normalized futures agent inputs | `futures_agent intake` | `signals` | other |
| `futures_agent_market_snapshots` | 0 | Feature / snapshot / lab feature storage | `futures_agent market provider` | `context reports` | feature |
| `futures_agent_migrations` | 7 | Schema migration bookkeeping | `futures_agent.db migrate` | `schema` | other |
| `futures_agent_post_multi_intent` | 0 | Supporting / auxiliary table | `research/futures_agent/telegram_multi_intent.py` | `research/futures_agent/schema.py` | other |
| `futures_agent_relative_strength` | 0 | Feature / snapshot / lab feature storage | `futures_agent` | `context_report` | feature |
| `futures_agent_research_market_data_cache` | 0 | Supporting / auxiliary table | `research/futures_agent/config.py` | `research/futures_agent/outcome_test_contamination_audit.py, research/futures_agent/historical_candles.py, research/futures_agent/schema_validate.py (+1 files)` | other |
| `futures_agent_research_signal_events` | 0 | Market / shock / news / lifecycle event data | `research/futures_agent/signal_outcome_outlier_audit.py` | `research/futures_agent/config.py, research/futures_agent/outcome_test_contamination_audit.py, research/futures_agent/signal_outcome_build.py (+3 files)` | event |
| `futures_agent_research_signal_markouts` | 0 | Supporting / auxiliary table | `research/futures_agent/signal_outcome_outlier_audit.py` | `research/futures_agent/config.py, research/futures_agent/outcome_test_contamination_audit.py, research/futures_agent/signal_outcome_build.py (+2 files)` | other |
| `futures_agent_research_signal_outcomes` | 0 | Strategy evaluation / validation / discovery results | `research/futures_agent/config.py` | `research/futures_agent/outcome_test_contamination_audit.py, research/futures_agent/signal_outcome_build.py, research/futures_agent/signal_outcome_report.py (+4 files)` | strategy_result |
| `futures_agent_signals` | 0 | Futures agent parsed signals | `bot/research/futures_agent` | `telegram / research CLIs` | other |
| `futures_agent_source_scores` | 0 | Supporting / auxiliary table | `research/futures_agent/config.py` | `research/futures_agent/research_scoring.py, research/futures_agent/source_ratings.py, research/futures_agent/schema_validate.py (+2 files)` | other |
| `futures_agent_source_scores_v2` | 0 | Supporting / auxiliary table | `research/futures_agent/config.py` | `research/futures_agent/source_ratings.py, research/futures_agent/schema_validate.py, research/futures_agent/__main__.py (+1 files)` | other |
| `futures_agent_targets` | 0 | Supporting / auxiliary table | `futures_agent` | `research` | other |
| `futures_agent_telegram_research_bridge` | 0 | Supporting / auxiliary table | `research/market_events/event_context_linker.py` | `research/futures_agent/db.py, research/futures_agent/config.py, research/futures_agent/telegram_inbound_audit.py (+3 files)` | other |
| `futures_agent_thesis_outcomes` | 0 | Strategy evaluation / validation / discovery results | `research/futures_agent/research_rebuild_theses.py` | `research/futures_agent/config.py, research/futures_agent/source_ratings.py, research/futures_agent/schema_validate.py (+3 files)` | strategy_result |
| `futures_agent_trader_levels` | 0 | Supporting / auxiliary table | `research/futures_agent/research_ingest.py` | `research/futures_agent/technical_levels_audit.py, research/futures_agent/thesis_quality_audit.py, research/futures_agent/research_rebuild_theses.py (+8 files)` | other |
| `futures_agent_trader_posts` | 0 | Supporting / auxiliary table | `telegram inbound` | `thesis / bridge` | other |
| `futures_agent_trader_theses` | 0 | Supporting / auxiliary table | `research/market_events/event_context_linker.py` | `research/futures_agent/research_ingest.py, research/futures_agent/technical_levels_audit.py, research/futures_agent/thesis_quality_audit.py (+8 files)` | other |

### `data/market_events.db`

LIVE market-events DB — collectors, G3, S40/S42/S55 ops (futures paper spine)

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `ai_paper_account_s47` | 0 | Supporting / auxiliary table | `research/ai_analyst/paper_trading/store.py` | `(see refs)` | other |
| `ai_paper_signals_s47` | 0 | Supporting / auxiliary table | `research/ai_analyst/paper_trading/store.py` | `(see refs)` | other |
| `ai_paper_trades_s47` | 0 | Paper / shadow / virtual trade ledger row store | `research/market_events/db_tools.py` | `research/ai_analyst/signal_consistency/repository.py, research/ai_analyst/paper_trading/store.py` | paper_trade |
| `ai_signal_history_s48` | 0 | Supporting / auxiliary table | `research/market_events/db_tools.py` | `research/ai_analyst/signal_consistency/repository.py, research/ai_analyst/strategy_validation/store.py, research/market_events/signal_intelligence/signal_paper_performance_s42.py` | other |
| `ai_signal_outcomes_s48` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/db_tools.py` | `research/ai_analyst/signal_consistency/repository.py, research/ai_analyst/strategy_validation/store.py` | strategy_result |
| `ai_signal_rankings_s48` | 0 | Supporting / auxiliary table | `research/ai_analyst/strategy_validation/store.py` | `(see refs)` | other |
| `market_asset_intelligence` | 120 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/narrative_engine/engine.py` | other |
| `market_calibration_daily_g34` | 1 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/score_breakdown_g34.py` | other |
| `market_candidate_g31` | 18920 | G31 trade candidates | `candidate_g31` | `G32 outcomes; scoring` | strategy_result |
| `market_candidate_outcomes_g32` | 18920 | G32 candidate outcomes | `candidate/outcome g32` | `validation; reports` | strategy_result |
| `market_daily_briefs` | 3 | Persisted report / digest artifact | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/narrative_engine/engine.py, research/market_events/signal_intelligence/news_intelligence/briefs.py, research/market_events/signal_intelligence/news_intelligence/reports.py` | report |
| `market_daily_report_g3` | 1 | Persisted report / digest artifact | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/daily_report_g3.py` | report |
| `market_decision_agent_outputs_s20` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/decision_engine_s20/persist.py` | other |
| `market_decision_runs_s20` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/decision_engine_s20/persist.py` | other |
| `market_event_ai_analyses` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/telegram_ops/cli.py, research/market_events/ai_analyst/reports.py (+8 files)` | event |
| `market_event_ai_analyses_f0` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/telegram_f0.py, research/market_events/signal_intelligence/telegram_f1.py (+6 files)` | event |
| `market_event_alert_log` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/market_event_alerts.py, research/market_events/schema_pg.py, research/market_events/telegram_ops/cli.py (+8 files)` | event |
| `market_event_analysis_jobs` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/process_manager.py, research/market_events/telegram_ops/cli.py (+7 files)` | event |
| `market_event_context` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_report.py` | `research/market_events/event_schema.py, research/market_events/alert_format.py, research/market_events/polymarket_audit.py (+8 files)` | event |
| `market_event_exchange_context` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/telegram_f0.py, research/market_events/signal_intelligence/signal_learning_s40.py (+8 files)` | event |
| `market_event_exchange_symbols` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/db_tools.py, research/market_events/schema_pg.py, research/market_events/signal_intelligence/exchange_resolver.py (+3 files)` | event |
| `market_event_lifecycle_decisions` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/strategy_matrix_report.py, research/market_events/lifecycle_decisions.py` | event |
| `market_event_snapshots` | 12 | Feature / snapshot / lab feature storage | `research/market_events/event_schema.py` | `research/market_events/market_snapshot.py, research/market_events/schema_pg.py, research/market_events/ai_analyst/context_bundle.py (+6 files)` | feature, event |
| `market_event_telegram_delivery_log` | 3 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/telegram_ops/retry.py, research/market_events/telegram_ops/cli.py (+3 files)` | event |
| `market_events` | 12 | Market / shock / news / lifecycle event data | `terminal/research/claude_review.py` | `terminal/scanner/providers.py, terminal/services/system_service.py, terminal/services/account_service.py (+8 files)` | event |
| `market_events_ai_comparisons` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/alert_engine/ai_comparison.py, research/market_events/alert_engine/dashboard_api.py` | event |
| `market_events_ai_research_g2` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/telegram_ops/cli.py, research/market_events/signal_intelligence/signal_generator_g3.py (+8 files)` | event |
| `market_events_alert_rankings_f3` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/signal_ranking_f3.py` | event |
| `market_events_candle_backfill_checkpoints` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/candle_backfill.py` | event |
| `market_events_candle_patterns_g51` | 3600 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/candle_pattern_g51.py` | event |
| `market_events_claude_requests_s50` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/research_terminal_s50.py` | event |
| `market_events_command_trace_g351` | 179 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/telegram_inbound_g04.py, research/market_events/signal_intelligence/sqlite_lock_smoke_g05.py` | event |
| `market_events_counterfactual_studies` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/counterfactual_reversal.py, research/market_events/schema_pg.py` | event |
| `market_events_digest_log` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/system_validation/dedupe_audit.py, research/market_events/alert_engine/weekly_report.py (+2 files)` | event, report |
| `market_events_discovery_runs` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/instrument_discovery.py, research/market_events/instrument_report.py` | event, strategy_result |
| `market_events_entity_registry` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/entity_graph.py, research/market_events/schema_pg.py` | event |
| `market_events_entry_stages_f3` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/entry_stages_f3.py` | event |
| `market_events_exhaustion` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/trend_shock_v2.py, research/market_events/signal_intelligence/exhaustion.py (+4 files)` | event |
| `market_events_f72_ops_state` | 0 | Worker / scheduler operational state | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/yesterday_report_f72.py` | other |
| `market_events_f73_ops_state` | 0 | Worker / scheduler operational state | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/reports_f73.py` | other |
| `market_events_funding_oi_history_f2` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/trend_shock_v2.py, research/market_events/signal_intelligence/funding_oi_history_f2.py (+2 files)` | event |
| `market_events_g1_pattern_stats` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/pattern_agent_s31.py, research/market_events/signal_intelligence/signal_generator_g3.py (+2 files)` | event, strategy_result |
| `market_events_g2_learning_notes` | 0 | Learning / experiment / evolution state | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/learning_g2.py` | learning, event |
| `market_events_g2_ops_state` | 0 | Worker / scheduler operational state | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/telegram_ops/cli.py, research/market_events/signal_intelligence/claude_ops_g2.py` | other |
| `market_events_g2_prompt_cache` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/claude_cache_g2.py` | event |
| `market_events_g2_visual_analysis` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/visual_analysis_g2.py` | event |
| `market_events_g3_ops_state` | 15 | Worker / scheduler operational state | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/health_g3.py, research/market_events/signal_intelligence/adaptive_learning_g3.py (+1 files)` | other |
| `market_events_historical_candles` | 6366 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/telegram_ops/synthetic_event.py, research/market_events/historical_replay/candle_backfill.py (+6 files)` | event |
| `market_events_inbound_trace_g04` | 2 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/process_manager.py, research/market_events/signal_intelligence/telegram_inbound_g04.py, research/market_events/signal_intelligence/telegram_diagnostics_s631.py (+1 files)` | event |
| `market_events_instruments` | 0 | Market / shock / news / lifecycle event data | `research/market_events/profile_shadow.py` | `research/market_events/event_schema.py, research/market_events/shock_opportunity_audit.py, research/market_events/observation_report.py (+8 files)` | event |
| `market_events_learning_daily_s43` | 1 | Learning / experiment / evolution state | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/signal_learning_s40.py` | learning, event |
| `market_events_liquidity_history_g51` | 4000 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/liquidity_history_g51.py` | event |
| `market_events_liquidity_trend_g1` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/learning_g2.py, research/market_events/signal_intelligence/telegram_g1.py (+4 files)` | event |
| `market_events_market_intelligence_f7` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/score_breakdown_g34.py, research/market_events/signal_intelligence/signal_learning_s40.py (+8 files)` | event |
| `market_events_migrations` | 66 | Schema migration bookkeeping | `market_events migrate CLI` | `schema` | other |
| `market_events_mtf_paper_runs` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py` | event |
| `market_events_mtf_replay_results` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py` | event |
| `market_events_mtf_trend_g1` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/liquidity_trend_g1.py` | event |
| `market_events_multitimeframe` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/opportunity_v2.py, research/market_events/signal_intelligence/ai_context_f0.py (+2 files)` | event |
| `market_events_near_miss_f73` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/reports_f73.py, research/market_events/signal_intelligence/near_miss_f73.py (+1 files)` | event |
| `market_events_near_miss_summaries` | 545102 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/near_miss_shadow.py, research/market_events/schema_pg.py` | event |
| `market_events_opportunity_scores` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/db_tools.py, research/market_events/market_event_alerts.py, research/market_events/schema_pg.py (+7 files)` | event |
| `market_events_opportunity_scores_v2` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/db_tools.py, research/market_events/market_event_alerts.py, research/market_events/schema_pg.py (+5 files)` | event |
| `market_events_paper_account_s42` | 1 | Market / shock / news / lifecycle event data | `signal_paper_performance_s42` | `paper-performance` | event |
| `market_events_paper_ops_state_s42` | 1 | Worker / scheduler operational state | `signal_paper_performance_s42` | `ops` | other |
| `market_events_paper_reports_s42` | 1 | Market / shock / news / lifecycle event data | `signal_paper_performance_s42` | `paper reports` | event, report |
| `market_events_paper_trades_s42` | 0 | Futures/market-events paper trade journal (OPEN/CLOSED) | `signal_paper_performance_s42` | `paper-performance CLI; S56 close bridge` | paper_trade |
| `market_events_pattern_stats_f72` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/yesterday_report_f72.py, research/market_events/signal_intelligence/signal_learning_f72.py` | event, strategy_result |
| `market_events_pending_shocks` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/db_tools.py, research/market_events/alert_format.py, research/market_events/pending_reversal.py (+8 files)` | event |
| `market_events_postmortem_ops_s56` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/trade_postmortem_s56.py` | event, strategy_result |
| `market_events_postmortem_runs_s56` | 0 | Market / shock / news / lifecycle event data | `trade_postmortem_s56` | `trade-postmortem` | event, strategy_result |
| `market_events_price_observations` | 0 | Market / shock / news / lifecycle event data | `research/market_events/profile_shadow.py` | `research/market_events/event_schema.py, research/market_events/shock_opportunity_audit.py, research/market_events/observation_report.py (+8 files)` | event |
| `market_events_profile_shadow_candidates` | 0 | Market / shock / news / lifecycle event data | `research/market_events/profile_shadow.py` | `research/market_events/event_schema.py, research/market_events/schema_pg.py` | event, strategy_result |
| `market_events_quant_reports_g50` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/quant_research_g50.py` | event, report |
| `market_events_replay_ai_critic` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/ai_critic.py` | event |
| `market_events_replay_context_links` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/ai_critic.py, research/market_events/historical_replay/context_linker.py (+1 files)` | event |
| `market_events_replay_path_metrics` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/strategy_matrix.py, research/market_events/historical_replay/ai_critic.py (+2 files)` | event |
| `market_events_replay_runs` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/strategy_matrix.py, research/market_events/historical_replay/ai_critic.py (+3 files)` | event |
| `market_events_replay_shocks` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/strategy_matrix.py, research/market_events/historical_replay/ai_critic.py (+3 files)` | event |
| `market_events_replay_splits` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/strategy_matrix.py, research/market_events/historical_replay/splits.py` | event |
| `market_events_replay_strategy_results` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/historical_replay/strategy_matrix.py` | event, strategy_result |
| `market_events_replay_timeline_g51` | 3325 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/replay_timeline_g51.py` | event |
| `market_events_research_artifacts_s50` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/research_artifacts_s50.py` | event |
| `market_events_reversal_learning_g1` | 0 | Learning / experiment / evolution state | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/reversal_learning_g1.py` | learning, event |
| `market_events_rule_suggestions_s56` | 0 | Market / shock / news / lifecycle event data | `trade_postmortem_s56 / S57` | `approve/reject suggestion` | event, strategy_result |
| `market_events_runner_state` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py` | event |
| `market_events_scheduler_state` | 1 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/heartbeat_diagnostics_g352.py, research/market_events/alert_engine/weekly_report.py (+2 files)` | event |
| `market_events_shadow_candidates` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/shock_f_v2_shadow.py, research/market_events/schema_pg.py, research/market_events/shock_f_shadow.py (+1 files)` | event, strategy_result |
| `market_events_signal_learning_s40_checkpoints` | 0 | Learning / experiment / evolution state | `signal_learning_s40` | `learning-worker` | learning, event |
| `market_events_signal_learning_s40_ops_state` | 5 | Learning / experiment / evolution state | `signal_learning_s40` | `learning-status` | learning |
| `market_events_signal_learning_s40_reviews` | 0 | Learning / experiment / evolution state | `signal_learning_s40 / review` | `learning` | learning, event |
| `market_events_signal_learning_s40_signals` | 0 | Normalized learning signals ingested for paper opens | `signal_learning_s40` | `S42 open; learning-worker` | learning, event |
| `market_events_signal_outcomes_f1` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/outcome_f1.py` | event, strategy_result |
| `market_events_signal_outcomes_f72` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/signal_followup_f72.py, research/market_events/signal_intelligence/signal_learning_s40.py (+5 files)` | event, strategy_result |
| `market_events_signal_priority_f5` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/priority_engine_f5.py, research/market_events/signal_intelligence/ops_reports_f71.py (+1 files)` | event |
| `market_events_signal_ranking_weekly` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/reports.py` | event |
| `market_events_signal_reports_f1` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/market_event_alerts.py, research/market_events/schema_pg.py, research/market_events/signal_intelligence/signal_report_f1.py` | event, report |
| `market_events_signal_reports_f2` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/market_event_alerts.py, research/market_events/schema_pg.py, research/market_events/signal_intelligence/signal_report_f2.py (+6 files)` | event, report |
| `market_events_signal_reports_f5` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/ops_reports_f71.py, research/market_events/signal_intelligence/reports_f73.py (+4 files)` | event, report |
| `market_events_signal_trace_f51` | 12 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/ops_reports_f71.py, research/market_events/signal_intelligence/signal_trace_f51.py (+1 files)` | event |
| `market_events_snapshot_history_g51` | 3800 | Feature / snapshot / lab feature storage | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/snapshot_history_g51.py` | feature, event |
| `market_events_timeline_cache` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/db_tools.py, research/market_events/schema_pg.py, research/market_events/alert_engine/timeline.py` | event |
| `market_events_trade_features_s55` | 0 | Open-time (+ finalized) feature vectors for paper trades | `trade_intelligence_s55` | `S56 packing; similarity gate` | feature |
| `market_events_trade_snapshots_s56` | 0 | Closed-trade analytics snapshots (research warehouse) | `trade_postmortem_s56; history_backfill_s621; S60 close` | `load_lab_trades; S59–S66` | feature |
| `market_events_trader_performance_f6` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/trader_performance_f6.py` | event |
| `market_events_trend_shock` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/trend_shock_v2.py, research/market_events/signal_intelligence/signal_ranking_f3.py (+5 files)` | event |
| `market_events_trend_shock_v2` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/trend_shock_v2.py, research/market_events/signal_intelligence/liquidity_trend_g1.py (+2 files)` | event |
| `market_events_universe_log` | 3 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/universe.py, research/market_events/observe_scope.py` | event |
| `market_events_visual_intel_f4` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/visual_intel_f4.py, research/market_events/signal_intelligence/visual_analysis_g2.py` | event |
| `market_experimental_followup_g39` | 0 | Learning / experiment / evolution state | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/experimental_g39.py` | learning |
| `market_experimental_signals_g39` | 0 | Learning / experiment / evolution state | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/experimental_g39.py, research/market_events/signal_intelligence/pipeline_audit_g0.py` | learning |
| `market_g35_candidate_state` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/telegram_intelligence_g35.py` | strategy_result |
| `market_g35_daily_research` | 1 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/telegram_intelligence_g35.py` | other |
| `market_intel_events` | 7 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/ai_analyst/context_builder.py, research/market_events/signal_intelligence/event_intelligence/engine.py, research/market_events/signal_intelligence/narrative_engine/engine.py` | event |
| `market_learning_dataset` | 0 | Learning / experiment / evolution state | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/shadow_g40.py, research/market_events/signal_intelligence/validation_signal_s11.py` | learning |
| `market_liquidity_state_g3` | 946 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/auto_validation_g4.py, research/market_events/signal_intelligence/liquidity_engine_g3.py (+2 files)` | other |
| `market_live_signals_g3` | 0 | G3 live signal stream | `runner_g3` | `S40 ingest` | other |
| `market_macro_events` | 7 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/ai_analyst/context_builder.py, research/market_events/signal_intelligence/multi_source/macro_collector.py, research/market_events/signal_intelligence/narrative_engine/quality.py` | event |
| `market_market_memory` | 140 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/market_memory_g36.py, research/market_events/signal_intelligence/pipeline_audit_g0.py (+1 files)` | other |
| `market_news_feed_n11` | 101 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/news_collector_n11.py, research/market_events/signal_intelligence/event_intelligence/engine.py, research/market_events/signal_intelligence/narrative_engine/engine.py (+1 files)` | event |
| `market_news_summary` | 12 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/narrative_engine/engine.py, research/market_events/signal_intelligence/news_intelligence/briefs.py, research/market_events/signal_intelligence/news_intelligence/aggregator.py (+1 files)` | other |
| `market_polymarket_signals` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/ai_analyst/context_builder.py, research/market_events/signal_intelligence/multi_source/polymarket_collector.py, research/market_events/signal_intelligence/narrative_engine/quality.py` | other |
| `market_research_dataset_g51` | 1000 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/research_dataset_g51.py` | other |
| `market_research_lake_builds_g51` | 2 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/research_dataset_g51.py` | other |
| `market_score_breakdown_g34` | 200214 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/score_breakdown_g34.py, research/market_events/signal_intelligence/auto_validation_g4.py` | strategy_result |
| `market_score_conflicts_g34` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/score_breakdown_g34.py` | other |
| `market_shadow_horizons` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/shadow_g40.py, research/market_events/signal_intelligence/pipeline_audit_g0.py` | other |
| `market_shadow_pipeline_trace` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/shadow_pipeline_g401.py, research/market_events/signal_intelligence/pipeline_audit_g0.py` | other |
| `market_shadow_signals` | 3 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/shadow_pipeline_g401.py, research/market_events/signal_intelligence/shadow_g40.py, research/market_events/signal_intelligence/pipeline_audit_g0.py` | other |
| `market_signal_followup_g3` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/signal_learning_s40.py, research/market_events/signal_intelligence/followup_g3.py` | other |
| `market_signal_inbox_s23` | 7 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/signal_inbox_s23.py` | event |
| `market_snapshots_g3` | 949 | Live market microstructure snapshots (funding/OI/etc.) | `recorder_g3 / market data` | `S55 enrichment; G3 signals` | feature |
| `market_source_health` | 8 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/multi_source/health.py` | other |
| `market_telegram_vision_g36` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/telegram_vision_g36.py, research/market_events/signal_intelligence/health_g36.py` | other |
| `market_top_assets` | 6 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/narrative_engine/engine.py` | other |
| `market_trend_windows_g3` | 7225 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/signal_learning_s40.py, research/market_events/signal_intelligence/trend_windows_g3.py (+3 files)` | other |
| `market_validation_analysis_g4` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/deterministic_research_g501.py, research/market_events/signal_intelligence/research_prompt_g50.py (+2 files)` | strategy_result |
| `market_validation_daily_g4` | 1 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/auto_validation_g4.py` | strategy_result |
| `market_validation_factor_stats_g4` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/deterministic_research_g501.py, research/market_events/signal_intelligence/auto_validation_g4.py (+1 files)` | strategy_result |
| `market_validation_horizons` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/validation_signal_s11.py` | strategy_result |
| `market_validation_optimizer_g4` | 0 | Filter / threshold / gate statistics | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/deterministic_research_g501.py, research/market_events/signal_intelligence/research_prompt_g50.py (+2 files)` | filter, strategy_result |
| `market_validation_recommendations_g4` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/deterministic_research_g501.py, research/market_events/signal_intelligence/auto_validation_g4.py` | strategy_result |
| `market_validation_records_g4` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/pattern_agent_s31.py, research/market_events/signal_intelligence/signal_learning_s40.py (+3 files)` | strategy_result |
| `market_validation_signals` | 1 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/validation_signal_s11.py` | strategy_result |
| `market_validation_symbol_stats_g4` | 0 | Strategy evaluation / validation / discovery results | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/auto_validation_g4.py` | strategy_result |
| `market_watchlist_g36` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/watchlist_g36.py` | other |
| `paper_strategy_runs` | 0 | Supporting / auxiliary table | `research/market_events/event_report.py` | `research/market_events/event_schema.py, research/market_events/shock_f_v2_shadow.py, research/market_events/pending_reversal.py (+8 files)` | other |
| `weight_history_g3` | 0 | Supporting / auxiliary table | `research/market_events/event_schema.py` | `research/market_events/schema_pg.py, research/market_events/signal_intelligence/adaptive_learning_g3.py` | other |

### `data/market_events_research.db`

RESEARCH sibling — S56–S62 analytics (local: 1786 hist snapshots)

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `market_events_alpha_discovery_runs_s62` | 0 | Market / shock / news / lifecycle event data | `alpha_discovery_s62` | `CLI` | event, strategy_result |
| `market_events_alpha_discovery_s62` | 0 | Market / shock / news / lifecycle event data | `alpha_discovery_s62` | `alpha-discovery CLI` | event, strategy_result |
| `market_events_feature_lab_runs_s59` | 0 | Feature / snapshot / lab feature storage | `feature_lab_s59` | `feature-lab CLI` | feature, event |
| `market_events_feature_lab_s59` | 0 | Feature lab ON/OFF metrics rows | `feature_lab_s59` | `intelligence-report` | feature, event |
| `market_events_postmortem_ops_s56` | 0 | Market / shock / news / lifecycle event data | `research/market_events/event_schema.py` | `research/market_events/signal_intelligence/trade_postmortem_s56.py` | event, strategy_result |
| `market_events_postmortem_runs_s56` | 0 | Market / shock / news / lifecycle event data | `trade_postmortem_s56` | `trade-postmortem` | event, strategy_result |
| `market_events_regime_ops_s57` | 0 | Filter / threshold / gate statistics | `market_regime_s57` | `ops` | filter, event |
| `market_events_regime_runs_s57` | 0 | Market / shock / news / lifecycle event data | `market_regime_s57` | `regime gate / reports` | event |
| `market_events_research_migrations` | 7 | Schema migration bookkeeping | `research_repository_s60` | `market-research-migrate` | other |
| `market_events_research_ops_s60` | 0 | Market / shock / news / lifecycle event data | `research_repository_s60` | `ops` | event |
| `market_events_rule_suggestions_s56` | 0 | Market / shock / news / lifecycle event data | `trade_postmortem_s56 / S57` | `approve/reject suggestion` | event, strategy_result |
| `market_events_strategy_discovery_runs_s61` | 0 | Market / shock / news / lifecycle event data | `strategy_discovery_s61` | `CLI` | event, strategy_result |
| `market_events_strategy_discovery_s61` | 0 | Market / shock / news / lifecycle event data | `strategy_discovery_s61` | `strategy-discovery CLI` | event, strategy_result |
| `market_events_trade_decisions_s58` | 0 | Decision / why-opened explainability traces | `decision_trace_s58 via S60` | `decision-report; lab enrich` | event |
| `market_events_trade_snapshots_s56` | 1786 | Closed-trade analytics snapshots (research warehouse) | `trade_postmortem_s56; history_backfill_s621; S60 close` | `load_lab_trades; S59–S66` | feature |

### `data/trades.db`

Polymarket bot primary DB — ER/shadow trades, features, learning stacks

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `ai_decisions` | 513 | Supporting / auxiliary table | `bot/ai_agent` | `strategy review` | other |
| `ai_features` | 513 | Feature / snapshot / lab feature storage | `bot/ai_agent` | `ai decisions` | feature |
| `bidirectional_shadow_observations` | 0 | Supporting / auxiliary table | `database.py` | `bidirectional_check.py, research/bidirectional_quote_alignment.py, research/bidirectional_live_audit.py (+3 files)` | other |
| `bidirectional_shadow_trades` | 0 | Paper / shadow / virtual trade ledger row store | `database.py` | `bidirectional_check.py, research/bidirectional_live_audit.py, ops/snapshot.py (+5 files)` | paper_trade |
| `bidirectional_shadow_v12_observations` | 0 | Supporting / auxiliary table | `—` | `—` | other |
| `bidirectional_shadow_v12_trades` | 0 | Supporting / auxiliary table | `strategy/bidirectional_observe_v12.py` | `research/market_events/dataset_provenance_audit.py, research/market_events/signal_intelligence/history_backfill_s621.py` | other |
| `brain_knowledge` | 21 | Learning / experiment / evolution state | `bot/trading_brain` | `evolution / review` | learning |
| `brain_learning_state` | 3 | Learning / experiment / evolution state | `bot/trading_brain` | `brain` | learning |
| `brain_memory` | 542 | Learning / experiment / evolution state | `bot/trading_brain` | `brain` | learning |
| `brain_trade_context` | 513 | Learning / experiment / evolution state | `bot/trading_brain` | `brain` | learning |
| `early_reversion_markets` | 831 | Paper / shadow / virtual trade ledger row store | `bot/early_reversion*.py` | `strategy / monitoring` | paper_trade |
| `early_reversion_trades` | 497 | Polymarket ER v1 trades | `bot/early_reversion.py` | `optimizer/scientist/brain; history_backfill_s621` | paper_trade |
| `early_reversion_v25_trades` | 221 | Polymarket ER v2.5 trades | `bot/early_reversion*.py` | `research/backfill` | paper_trade |
| `early_reversion_v2_trades` | 513 | Polymarket ER v2 closed/open trades | `bot/early_reversion_v2.py` | `optimizer/scientist/brain; history_backfill_s621; PROJECT_OS polymarket archive` | paper_trade |
| `early_reversion_v3_trades` | 282 | Polymarket ER v3 trades | `bot/early_reversion*.py (v3 path)` | `research/backfill; analytics` | paper_trade |
| `er_ask_level_counters` | 12 | Supporting / auxiliary table | `er_stats.py` | `(see refs)` | other |
| `er_funnel_events` | 13420 | Market / shock / news / lifecycle event data | `ER funnel logging` | `ops/diagnostics` | event |
| `er_health_events` | 10 | Market / shock / news / lifecycle event data | `recovery_health.py` | `database.py, research/execution_failure_audit.py, report/analytics.py` | event |
| `er_strategy_counters` | 2 | Supporting / auxiliary table | `database.py` | `er_stats.py, research/execution_failure_audit.py` | other |
| `er_timing_counters` | 10 | Supporting / auxiliary table | `er_stats.py` | `(see refs)` | other |
| `evolution_history` | 0 | Learning / experiment / evolution state | `bot/evolution` | `ops` | learning |
| `evolution_observe_stats` | 0 | Learning / experiment / evolution state | `database.py` | `evolution/observe_stats.py` | learning |
| `evolution_regime_shadow` | 0 | Learning / experiment / evolution state | `database.py` | `evolution/regime_shadow.py, evolution/history.py` | learning |
| `evolution_regime_shadow_trades` | 0 | Paper / shadow / virtual trade ledger row store | `database.py` | `evolution/regime_shadow.py` | paper_trade, learning |
| `evolution_shadow` | 2 | Learning / experiment / evolution state | `bot/evolution` | `auto_review` | learning |
| `evolution_shadow_evaluations` | 0 | Learning / experiment / evolution state | `bot/evolution` | `auto_review` | learning |
| `futures_research_signals` | 0 | Supporting / auxiliary table | `research/futures/parse_pipeline.py` | `research/futures/schema.py` | other |
| `futures_signal_outcomes` | 0 | Strategy evaluation / validation / discovery results | `research/futures/schema.py` | `(see refs)` | strategy_result |
| `futures_signal_parse_audit` | 0 | Supporting / auxiliary table | `research/futures/schema.py` | `(see refs)` | other |
| `futures_signal_recommendations` | 0 | Supporting / auxiliary table | `research/futures/schema.py` | `(see refs)` | other |
| `futures_signal_snapshots` | 0 | Feature / snapshot / lab feature storage | `research/futures/schema.py` | `(see refs)` | feature |
| `futures_signal_targets` | 0 | Supporting / auxiliary table | `research/futures/schema.py` | `(see refs)` | other |
| `live_journal` | 0 | Supporting / auxiliary table | `bot/portfolio / live` | `ops` | other |
| `market_checks` | 223724 | Market polling / eligibility checks | `bot/main loop market poll` | `ER entry gate` | other |
| `mb_edge_statistics` | 2600 | Supporting / auxiliary table | `research/market_behavior/schema.py` | `(see refs)` | other |
| `mb_late_window` | 0 | Supporting / auxiliary table | `research/market_behavior/schema.py` | `(see refs)` | other |
| `mb_market_summary` | 0 | Supporting / auxiliary table | `research/market_behavior/schema.py` | `(see refs)` | other |
| `mb_tp_probability` | 0 | Supporting / auxiliary table | `research/market_behavior/config.py` | `research/market_behavior/schema.py` | other |
| `mtf_market_metadata` | 0 | Supporting / auxiliary table | `research/mtf/metadata.py` | `(see refs)` | other |
| `multi_timeframe_snapshots` | 0 | Feature / snapshot / lab feature storage | `research/market_events/polymarket_audit.py` | `research/mtf/snapshots.py, research/mtf/timeframe_research.py, research/mtf/report.py (+2 files)` | feature |
| `no_c_filter_live_counters` | 1 | Filter / threshold / gate statistics | `NO_C live filter` | `ops` | filter |
| `no_c_filter_shadow_counters` | 12 | Filter / threshold / gate statistics | `NO_C filter shadow` | `filter research` | filter |
| `no_c_filter_shadow_entries` | 91 | Filter / threshold / gate statistics | `NO_C filter shadow` | `filter research` | filter |
| `order_fill_audit` | 187 | Supporting / auxiliary table | `bot/execution` | `ops` | other |
| `order_intents` | 254 | Supporting / auxiliary table | `bot/execution path` | `audit` | other |
| `portfolio_state` | 1 | Supporting / auxiliary table | `bot/portfolio` | `live readiness` | other |
| `scientist_experiments` | 27 | Learning / experiment / evolution state | `bot/scientist` | `evolution` | learning |
| `scientist_hypotheses` | 27 | Learning / experiment / evolution state | `bot/scientist` | `evolution` | learning |
| `scientist_patterns` | 0 | Learning / experiment / evolution state | `bot/scientist` | `evolution` | learning |
| `ss_discovered_strategies` | 0 | Supporting / auxiliary table | `ops/snapshot.py` | `research/strategy_simulator/storage.py` | other |
| `ss_shadow_candidates` | 0 | Strategy evaluation / validation / discovery results | `ops/snapshot.py` | `research/strategy_simulator/finalists.py, research/strategy_simulator/storage.py` | strategy_result |
| `ss_simulation_results` | 0 | Strategy evaluation / validation / discovery results | `ops/snapshot.py` | `research/strategy_simulator/storage.py` | strategy_result |
| `trade_features` | 513 | Per-trade feature dump (polymarket) | `feature extractors on close` | `ai_agent; brain; history_backfill` | feature |
| `v4_shadow_observations` | 44774 | Supporting / auxiliary table | `v4 shadow strategy` | `diagnostics` | other |
| `v4_shadow_trades` | 107 | V4 shadow trades | `v4 shadow strategy` | `history_backfill_s621` | paper_trade |
| `virtual_trades` | 48 | Virtual / simulated polymarket trades | `virtual / paper polymarket path` | `history_backfill_s621` | paper_trade |
| `yes_c_shadow_trades` | 124 | YES_C shadow strategy trades | `shadow / strategy filter paths` | `history_backfill_s621` | paper_trade |

### `data/trades_before_live.db`

Snapshot backup of trades.db (pre-live)

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `early_reversion_markets` | 831 | Paper / shadow / virtual trade ledger row store | `bot/early_reversion*.py` | `strategy / monitoring` | paper_trade |
| `early_reversion_trades` | 497 | Polymarket ER v1 trades | `bot/early_reversion.py` | `optimizer/scientist/brain; history_backfill_s621` | paper_trade |
| `early_reversion_v25_trades` | 221 | Polymarket ER v2.5 trades | `bot/early_reversion*.py` | `research/backfill` | paper_trade |
| `early_reversion_v2_trades` | 410 | Polymarket ER v2 closed/open trades | `bot/early_reversion_v2.py` | `optimizer/scientist/brain; history_backfill_s621; PROJECT_OS polymarket archive` | paper_trade |
| `early_reversion_v3_trades` | 282 | Polymarket ER v3 trades | `bot/early_reversion*.py (v3 path)` | `research/backfill; analytics` | paper_trade |
| `market_checks` | 144349 | Market polling / eligibility checks | `bot/main loop market poll` | `ER entry gate` | other |
| `order_intents` | 3 | Supporting / auxiliary table | `bot/execution path` | `audit` | other |
| `virtual_trades` | 48 | Virtual / simulated polymarket trades | `virtual / paper polymarket path` | `history_backfill_s621` | paper_trade |

### `data/trades_before_no_c_yes_b.db`

Snapshot backup (pre NO_C/YES_B change)

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `early_reversion_markets` | 350 | Paper / shadow / virtual trade ledger row store | `bot/early_reversion*.py` | `strategy / monitoring` | paper_trade |
| `early_reversion_trades` | 342 | Polymarket ER v1 trades | `bot/early_reversion.py` | `optimizer/scientist/brain; history_backfill_s621` | paper_trade |
| `early_reversion_v25_trades` | 82 | Polymarket ER v2.5 trades | `bot/early_reversion*.py` | `research/backfill` | paper_trade |
| `early_reversion_v2_trades` | 268 | Polymarket ER v2 closed/open trades | `bot/early_reversion_v2.py` | `optimizer/scientist/brain; history_backfill_s621; PROJECT_OS polymarket archive` | paper_trade |
| `early_reversion_v3_trades` | 133 | Polymarket ER v3 trades | `bot/early_reversion*.py (v3 path)` | `research/backfill; analytics` | paper_trade |
| `market_checks` | 86083 | Market polling / eligibility checks | `bot/main loop market poll` | `ER entry gate` | other |
| `virtual_trades` | 15 | Virtual / simulated polymarket trades | `virtual / paper polymarket path` | `history_backfill_s621` | paper_trade |

### `data/trades_before_only_no_c.db`

Snapshot backup (only NO_C era)

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `early_reversion_markets` | 486 | Paper / shadow / virtual trade ledger row store | `bot/early_reversion*.py` | `strategy / monitoring` | paper_trade |
| `early_reversion_trades` | 439 | Polymarket ER v1 trades | `bot/early_reversion.py` | `optimizer/scientist/brain; history_backfill_s621` | paper_trade |
| `early_reversion_v25_trades` | 179 | Polymarket ER v2.5 trades | `bot/early_reversion*.py` | `research/backfill` | paper_trade |
| `early_reversion_v2_trades` | 365 | Polymarket ER v2 closed/open trades | `bot/early_reversion_v2.py` | `optimizer/scientist/brain; history_backfill_s621; PROJECT_OS polymarket archive` | paper_trade |
| `early_reversion_v3_trades` | 230 | Polymarket ER v3 trades | `bot/early_reversion*.py (v3 path)` | `research/backfill; analytics` | paper_trade |
| `market_checks` | 103100 | Market polling / eligibility checks | `bot/main loop market poll` | `ER entry gate` | other |
| `virtual_trades` | 29 | Virtual / simulated polymarket trades | `virtual / paper polymarket path` | `history_backfill_s621` | paper_trade |

### `data/trades_before_yesc_20260628_2145.db`

Snapshot backup (pre YES_C 2026-06-28)

| Table | Rows | Purpose | Producer module | Consumer module | Categories |
|-------|-----:|---------|-----------------|-----------------|------------|
| `early_reversion_markets` | 831 | Paper / shadow / virtual trade ledger row store | `bot/early_reversion*.py` | `strategy / monitoring` | paper_trade |
| `early_reversion_trades` | 497 | Polymarket ER v1 trades | `bot/early_reversion.py` | `optimizer/scientist/brain; history_backfill_s621` | paper_trade |
| `early_reversion_v25_trades` | 221 | Polymarket ER v2.5 trades | `bot/early_reversion*.py` | `research/backfill` | paper_trade |
| `early_reversion_v2_trades` | 445 | Polymarket ER v2 closed/open trades | `bot/early_reversion_v2.py` | `optimizer/scientist/brain; history_backfill_s621; PROJECT_OS polymarket archive` | paper_trade |
| `early_reversion_v3_trades` | 282 | Polymarket ER v3 trades | `bot/early_reversion*.py (v3 path)` | `research/backfill; analytics` | paper_trade |
| `er_ask_level_counters` | 6 | Supporting / auxiliary table | `er_stats.py` | `(see refs)` | other |
| `er_strategy_counters` | 1 | Supporting / auxiliary table | `database.py` | `er_stats.py, research/execution_failure_audit.py` | other |
| `er_timing_counters` | 5 | Supporting / auxiliary table | `er_stats.py` | `(see refs)` | other |
| `market_checks` | 211534 | Market polling / eligibility checks | `bot/main loop market poll` | `ER entry gate` | other |
| `order_intents` | 128 | Supporting / auxiliary table | `bot/execution path` | `audit` | other |
| `v4_shadow_observations` | 33696 | Supporting / auxiliary table | `v4 shadow strategy` | `diagnostics` | other |
| `v4_shadow_trades` | 37 | V4 shadow trades | `v4 shadow strategy` | `history_backfill_s621` | paper_trade |
| `virtual_trades` | 48 | Virtual / simulated polymarket trades | `virtual / paper polymarket path` | `history_backfill_s621` | paper_trade |

## Notes on producer / consumer columns

- **Producer / Consumer** are best-effort from known spine mappings + static string references under `bot/`.
- Empty tables may still have producers registered; row count 0 means no data on this machine.
- Snapshot DBs (`trades_before_*`) have the same producers historically as `trades.db` but are not written by current runtime.
- File reports under `research/reports/` are **not** SQLite tables (gitignored filesystem artifacts).

## Related PROJECT_OS docs

- `CURRENT_STATE.md` — futures_paper vs polymarket_hist
- `POLYMARKET_ARCHIVE.md` — hist 1786
- `NEXT_TASK.md` — locate the ~28k futures_paper DB

