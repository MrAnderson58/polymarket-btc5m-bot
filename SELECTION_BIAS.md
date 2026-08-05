# SELECTION_BIAS

- **selection_bias** [HIGH] score=78.59 ← LARGEST
  Book B accepts 4111/19205 (21.4% of lake/journal). Elite portrait is conditioned on Decision accept.
- **look_ahead_bias** [MEDIUM] score=50.0
  elite_candidates.learned_score adjusted from closed pnl (Score++/--). Category after learn can use future outcome — base_score is safer for research.
- **book_bias** [HIGH] score=48.42
  SHORT elite=98.1% ≈ Decision accepted SHORT=98.1%, corpus SHORT=49.7% → dominance inherited from Decision Book, not profile bug.
- **survivorship_bias** [MEDIUM] score=30.83
  Elite WR=76.23 vs corpus WR=45.4 — expected if Decision selects winners; verify not outcome-leaked into score.
- **data_leakage** [HIGH] score=1.0
  1 stored rows have base_score<80 but score>=80 after outcome learning — outcome leaked into membership.
