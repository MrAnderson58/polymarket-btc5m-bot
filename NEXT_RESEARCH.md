# NEXT_RESEARCH

Autonomous mode: rank calculations that maximize expected EV gain.
Never reopen SQLite / Research Lake / logs / optimizer.
Never repeat already implemented research listed in the package.

Context: top_rejector=Replay recoverable_ev=16940.4952

## TOP 5 new mathematical research

### 1. Conditional EV of Replay FN given Timeline+Fingerprint pass
- Why: Largest rejector is often Replay; measure recoverable EV only when other modules already pass.
- Expected EV gain: High — isolates true Replay false-negatives.
- Math: E[PnL | replay_reject, timeline≥τ, fp≥φ] with bootstrap CI
- Not implemented: true (excluded from implemented_research n=25)

### 2. Session×coin Replay NULL coverage rate
- Why: Missing Replay scores may concentrate by session/coin.
- Expected EV gain: Medium-High — coverage fixes beat threshold tweaks.
- Math: coverage = n(replay IS NULL)/n by (symbol, session_bucket)
- Not implemented: true (excluded from implemented_research n=25)

### 3. Module influence ≠ first-rejector reconciliation matrix
- Why: Waterfall first-rejector overstates early modules.
- Expected EV gain: High — better priority for math filters.
- Math: ΔWR/ΔEV removing one module vs sequential reject counts
- Not implemented: true (excluded from implemented_research n=25)

### 4. Book D sparsity attribution under Feature Store gate
- Why: Book D often empty; need filter vs FS interaction math.
- Expected EV gain: Medium — unlocks elite math book density.
- Math: survival table per filter with leave-one-out acceptance
- Not implemented: true (excluded from implemented_research n=25)

### 5. Fingerprint–Timeline joint interval features (new intervals only)
- Why: Current package has compact FP/TL heads; joint bins unused.
- Expected EV gain: Medium — new features without touching Gate/Strategy.
- Math: binned joint (fp_sim, tl_sim) × forward EV heatmap
- Not implemented: true (excluded from implemented_research n=25)

## Autonomous choice (max EV)

**Selected:** Conditional EV of Replay FN given Timeline+Fingerprint pass
Reason: Largest rejector is often Replay; measure recoverable EV only when other modules already pass.

## Cost policy

If required inputs exceed 40000 tokens, ask Python to aggregate first.
Never request full trade history.
