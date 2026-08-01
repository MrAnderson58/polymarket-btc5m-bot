# Research Lake Schema V1

- schema_version: `1.0.0`
- dataset_version: `rlake-v1`
- feature_version: `v1`

## Table `market_events_research_lake_v1`

| Column | Meaning |
|---|---|
| trade_id | S42 paper trade id (PK) |
| symbol / direction | Instrument + side |
| entry / exit / result / pnl | Outcome fields |
| gate / confidence / regime | Decision context |
| features_json | Numeric/categorical feature blob |
| macro_json | Funding / OI / Fear / macro |
| news_json | News / AI scores |
| patterns_json | Pattern / regime / G31 / S56 |
| alpha_labels_json | Alpha validation/discovery labels |
| optimizer_state_json | Optimizer ops snapshot |
| experiment_state_json | Recent experiments |
| feature_version / dataset_version / schema_version | Version triad |

## Sources joined

- S42 paper trades (hub)
- S55 trade features
- S56 postmortem snapshots
- G31 candidates (time/symbol proximity)
- Feature Store extract_sample
- Alpha / optimizer / experiment state (best-effort)
