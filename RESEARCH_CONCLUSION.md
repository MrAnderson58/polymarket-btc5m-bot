# RESEARCH_CONCLUSION

## 1. Executive Summary

- Package generated_at=1786087656 bytes=79643
- Reality score=85.98 lake_rows=19205.0
- Elite n=4111 categories={'ELITE': 1204, 'A+': 1659, 'A': 1248}
- Replay recoverable_ev=16940.4952 protected_ev=831.9049
- Top funnel rejector=Replay n=19155
- Book B stats={'trades': 4111, 'wr': 76.23, 'pf': None, 'pf_inf': True, 'ev': 1.5337, 'sharpe': 1.0103, 'max_dd': 0.0, 'total': 6305.2284, 'n_rows': 19205, 'n_accepted': 4111}
- Samples closed=100 accepted=30 rejected=30
- Mode=offline_template (Claude not invoked)
- research_only=true no_strategy_change=true

## 2. Today's Findings

1. Compact package built without reading the full Research Lake.
2. Replay remains the dominant first-rejector when funnel stats are present.
3. Reality binding is consumed as a scalar score + dataset meta only.
4. Elite corpus size and category mix are summarized statistically.
5. Book statistics are aggregates (WR/PF/EV/Sharpe), not raw rows.
6. Last-N accepted/rejected samples are slim fields only.
7. Forward/morning content included as truncated report heads when available.
8. Fingerprint/Timeline included as compact summaries / report heads.
9. Token budget targets: <50k input, <10k output.
10. Offline conclusion used when Claude API is blocked or unconfigured.

## 3. Top Mathematical Discoveries

- Discoveries require Hermes LLM pass for narrative; offline mode reports only package metrics.
- Net Replay EV (protected−recoverable) if present: -16108.5903

## 4. Rejected Trades Analysis

- n last_30_rejected=30
- Inspect slim module scores (replay/timeline/fingerprint/brain) in package samples.

## 5. Accepted Trades Analysis

- n last_30_accepted=30
- Compare confidence / historical_wr / historical_ev distributions in package.

## 6. Replay Investigation

- recoverable_ev=16940.4952
- protected_ev=831.9049
- largest_mistake={'trade_id': 19201, 'symbol': 'WIF', 'pnl': 77.7242, 'replay': None, 'reason': 'replay_missing', 'regime': 'RANGE'}

## 7. Reality Validation

- reality_score=85.98
- dataset hash/version/rows bound in package.reality

## 8. Elite Review

- n_elite=4111 stats={'trades': 4111, 'wr': 76.23, 'pf': None, 'pf_inf': True, 'ev': 1.5337, 'sharpe': 1.0103, 'max_dd': 0.0, 'total': 6305.2284}

## 9. New Hypotheses

### H1
- Reason: Replay missing scores drive FN recoverable EV.
- Expected Improvement: Higher attribution clarity for Replay coverage.
- Required Sample Size: ≥1000 Replay-missing closed trades.
- Expected Validation Method: Replay Recovery + Reality rebind on same lake hash.

### H2
- Reason: Funnel first-rejector ≠ sole causal module.
- Expected Improvement: Better module influence vs waterfall interpretation.
- Required Sample Size: full Book A corpus on integrity-bound lake.
- Expected Validation Method: Decision Funnel module_influence deltas.

### H3
- Reason: Book D sparsity may be Feature Store / filter interaction.
- Expected Improvement: Clearer math-book bottleneck ranking.
- Required Sample Size: all math-book candidates under frozen filters.
- Expected Validation Method: paper-math-report + integrity Book D gate.

## 10. Recommended Mathematics

- Coverage rate of Replay NULL vs below-floor rejects by coin/session.
- Conditional EV of Replay FN given Timeline/Fingerprint pass.
- Bootstrap CI on recoverable_ev and protected_ev.

## 11. Research Priority

- P1: Replay missing-score coverage mathematics
- P2: Funnel influence vs first-rejector reconciliation
- P3: Book D sparsity attribution under Feature Store gate
