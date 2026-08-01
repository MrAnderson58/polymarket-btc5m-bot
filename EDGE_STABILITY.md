# EDGE_STABILITY

Walk-forward / rolling / expanding / OOS / regime-slice survival.

| rule | WF | rolling | expanding | OOS | regime | stability | CV |
|---|---|---|---|---|---|---|---|
| `(atr_pct<=q25(633.4)) AND (direction==LONG)` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (regime==RANGE)` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0)` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24))` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RAN` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (weekday==0.0)` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(direction==LONG) AND (regime==RANGE) AND (atr_pct<=q25(633.` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(weekday==0.0) AND (atr_pct<=q25(633.4)) AND (direction==LON` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (direction==LO` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (direction==LON` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (regime==RANGE)` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (direction==` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (regime==RAN` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (direction==LON` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (regime==RANGE)` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (direction==` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (regime==RAN` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (weekday==0.0)` | True | True | True | True | True | 0.2644 | 1.2697 |
| `(atr_pct<=q25(633.4)) AND (gate_decision==INSUFFICIENT_HISTO` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(gate_decision==INSUFFICIENT_HISTORY) AND (regime==RANGE) AN` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(gate_decision==INSUFFICIENT_HISTORY) AND (regime==RANGE) AN` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(regime==RANGE) AND (gate_decision==INSUFFICIENT_HISTORY) AN` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(regime==RANGE) AND (gate_decision==INSUFFICIENT_HISTORY) AN` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (gate_decision` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (gate_decisi` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RAN` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(weekday==0.0) AND (atr_pct<=q25(633.4)) AND (direction==LON` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (gate_decision==INSUFFICIENT_HISTO` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (direction==LO` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (regime==RAN` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (gate_decision` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (gate_decision=` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (direction==LON` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (gate_decision=` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (gate_decisi` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (gate_decisi` | True | True | False | True | True | 0.2587 | 1.3192 |
| `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (gate_decisi` | True | True | False | True | True | 0.2587 | 1.3192 |
