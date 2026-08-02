# MODULE_ATTRIBUTION

Per-module contribution on CLOSED S42 / research-lake trades vs production baseline.

| module | ΔEV | ΔPF | ΔWR | MI | IG | Prec | Rec | F1 | grade |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `production` | 0.0 | 0.0 | 0.0 | 0.155276 | 0.155276 | 0.1313 | 0.2875 | 0.1803 | BASE |
| `replay` | 9024.4071 | 1.9097 | 0.4129 | 0.990774 | 0.990774 | 0.54 | 0.0031 | 0.0061 | A+ |
| `edge` | 23041.5482 | 7.4177 | 0.3217 | 0.99396 | 0.99396 | 0.4543 | 0.9969 | 0.6241 | A+ |
| `alpha` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `optimizer` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `brain` | 10009.8632 | None | 0.4131 | 0.99396 | 0.99396 | 1.0 | 0.0031 | 0.0062 | REMOVE |
| `causality` | 27541.887 | None | 0.8674 | 0.0 | 0.0 | 1.0 | 1.0 | 1.0 | A+ |
| `evolution` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `features` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `validation` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |

## Solo metrics

```json
[
  {
    "module": "production",
    "n": 19160,
    "ev": -7759.6938,
    "mean_ev": -0.404994,
    "pf": 0.3737,
    "wr": 0.1326,
    "mutual_information": 0.155276,
    "information_gain": 0.155276,
    "precision": 0.1313,
    "recall": 0.2875,
    "f1": 0.1803,
    "delta_ev": 0.0,
    "delta_pf": 0.0,
    "delta_wr": 0.0,
    "vs_production": {
      "delta_ev": 0.0,
      "delta_pf": 0.0,
      "delta_wr": 0.0,
      "delta_mi": 0.0,
      "delta_ig": 0.0,
      "delta_f1": 0.0
    },
    "research_value": 0.1803
  },
  {
    "module": "replay",
    "n": 19160,
    "ev": 1264.7133,
    "mean_ev": 0.066008,
    "pf": 2.2834,
    "wr": 0.5455,
    "mutual_information": 0.990774,
    "information_gain": 0.990774,
    "precision": 0.54,
    "recall": 0.0031,
    "f1": 0.0061,
    "delta_ev": 9024.4071,
    "delta_pf": 1.9097,
    "delta_wr": 0.4129,
    "vs_production": {
      "delta_ev": 9024.4071,
      "delta_pf": 1.9097,
      "delta_wr": 0.4129,
      "delta_mi": 0.835498,
      "delta_ig": 0.835498,
      "delta_f1": -0.1742
    },
    "research_value": 0.0061,
    "grade": "A+",
    "score": 33.3535,
    "keep": true,
    "remove": false
  },
  {
    "module": "edge",
    "n": 19160,
    "ev": 15281.8544,
    "mean_ev": 0.797592,
    "pf": 7.7914,
    "wr": 0.4543,
    "mutual_information": 0.99396,
    "information_gain": 0.99396,
    "precision": 0.4543,
    "recall": 0.9969,
    "f1": 0.6241,
    "delta_ev": 23041.5482,
    "delta_pf": 7.4177,
    "delta_wr": 0.3217,
    "vs_production": {
      "delta_ev": 23041.5482,
      "delta_pf": 7.4177,
      "delta_wr": 0.3217,
      "delta_mi": 0.838684,
      "delta_ig": 0.838684,
      "delta_f1": 0.4438
    },
    "research_value": 0.6241,
    "grade": "A+",
    "score": 39.9711,
    "keep": true,
    "remove": false
  },
  {
    "module": "alpha",
    "n": 19160,
    "ev": 0.0,
    "mean_ev": 0.0,
    "pf": null,
    "wr": 0.5457,
    "mutual_information": 0.99396,
    "information_gain": 0.99396,
    "precision": null,
    "recall": 0.0,
    "f1": null,
    "delta_ev": 7759.6938,
    "delta_pf": null,
    "delta_wr": 0.4131,
    "vs_production": {
      "delta_ev": 7759.6938,
      "delta_pf": null,
      "delta_wr": 0.4131,
      "delta_mi": 0.838684,
      "delta_ig": 0.838684,
      "delta_f1": null
    },
    "research_value": 0.0,
    "grade": "REMOVE",
    "score": -10.0302,
    "keep": false,
    "remove": true
  },
  {
    "module": "optimizer",
    "n": 19160,
    "ev": 0.0,
    "mean_ev": 0.0,
    "pf": null,
    "wr": 0.5457,
    "mutual_information": 0.99396,
    "information_gain": 0.99396,
    "precision": null,
    "recall": 0.0,
    "f1": null,
    "delta_ev": 7759.6938,
    "delta_pf": null,
    "delta_wr": 0.4131,
    "vs_production": {
      "delta_ev": 7759.6938,
      "delta_pf": null,
      "delta_wr": 0.4131,
      "delta_mi": 0.838684,
      "delta_ig": 0.838684,
      "delta_f1": null
    },
    "research_value": 0.0,
    "grade": "REMOVE",
    "score": -5.0302,
    "keep": false,
    "remove": true
  },
  {
    "module": "brain",
    "n": 19160,
    "ev": 2250.1694,
    "mean_ev": 0.117441,
    "pf": null,
    "wr": 0.5457,
    "mutual_information": 0.99396,
    "information_gain": 0.99396,
    "precision": 1.0,
    "recall": 0.0031,
    "f1": 0.0062,
    "delta_ev": 10009.8632,
    "delta_pf": null,
    "delta_wr": 0.4131,
    "vs_production": {
      "delta_ev": 10009.8632,
      "delta_pf": null,
      "delta_wr": 0.4131,
      "delta_mi": 0.838684,
      "delta_ig": 0.838684,
      "delta_f1": -0.1741
    },
    "research_value": 0.0062,
    "grade": "REMOVE",
    "score": -28.4213,
    "keep": false,
    "remove": true
  },
  {
    "module": "causality",
    "n": 19160,
    "ev": 19782.1932,
    "mean_ev": 1.032474,
    "pf": null,
    "wr": 1.0,
    "mutual_information": 0.0,
    "information_gain": 0.0,
    "precision": 1.0,
    "recall": 1.0,
    "f1": 1.0,
    "delta_ev": 27541.887,
    "delta_pf": null,
    "delta_wr": 0.8674,
    "vs_production": {
      "delta_ev": 27541.887,
      "delta_pf": null,
      "delta_wr": 0.8674,
      "delta_mi": -0.155276,
      "delta_ig": -0.155276,
      "delta_f1": 0.8197
    },
    "research_value": 1.0,
    "grade": "A+",
    "score": 75.0,
    "keep": true,
    "remove": false
  },
  {
    "module": "evolution",
    "n": 19160,
    "ev": 0.0,
    "mean_ev": 0.0,
    "pf": null,
    "wr": 0.5457,
    "mutual_information": 0.99396,
    "information_gain": 0.99396,
    "precision": null,
    "recall": 0.0,
    "f1": null,
    "delta_ev": 7759.6938,
    "delta_pf": null,
    "delta_wr": 0.4131,
    "vs_production": {
      "delta_ev": 7759.6938,
      "delta_pf": null,
      "delta_wr": 0.4131,
      "delta_mi": 0.838684,
      "delta_ig": 0.838684,
      "delta_f1": null
    },
    "research_value": 0.0,
    "grade": "REMOVE",
    "score": -16.6113,
    "keep": false,
    "remove": true
  },
  {
    "module": "features",
    "n": 19160,
    "ev": 0.0,
    "mean_ev": 0.0,
    "pf": null,
    "wr": 0.5457,
    "mutual_information": 0.99396,
    "information_gain": 0.99396,
    "precision": null,
    "recall": 0.0,
    "f1": null,
    "delta_ev": 7759.6938,
    "delta_pf": null,
    "delta_wr": 0.4131,
    "vs_production": {
      "delta_ev": 7759.6938,
      "delta_pf": null,
      "delta_wr": 0.4131,
      "delta_mi": 0.838684,
      "delta_ig": 0.838684,
      "delta_f1": null
    },
    "research_value": 0.0,
    "grade": "REMOVE",
    "score": -5.0302,
    "keep": false,
    "remove": true
  },
  {
    "module": "validation",
    "n": 19160,
    "ev": 0.0,
    "mean_ev": 0.0,
    "pf": null,
    "wr": 0.5457,
    "mutual_information": 0.99396,
    "information_gain": 0.99396,
    "precision": null,
    "recall": 0.0,
    "f1": null,
    "delta_ev": 7759.6938,
    "delta_pf": null,
    "delta_wr": 0.4131,
    "vs_production": {
      "delta_ev": 7759.6938,
      "delta_pf": null,
      "delta_wr": 0.4131,
      "delta_mi": 0.838684,
      "delta_ig": 0.838684,
      "delta_f1": null
    },
    "research_value": 0.0,
    "grade": "REMOVE",
    "score": -5.0302,
    "keep": false,
    "remove": true
  }
]
```

## Incremental chain

```json
[
  {
    "step": 0,
    "added": null,
    "modules": [
      "production"
    ],
    "metrics": {
      "n": 19160,
      "ev": -7759.6938,
      "mean_ev": -0.404994,
      "pf": 0.3737,
      "wr": 0.1326,
      "mutual_information": 0.155276,
      "information_gain": 0.155276,
      "precision": 0.1313,
      "recall": 0.2875,
      "f1": 0.1803
    },
    "delta_vs_prev": null,
    "delta_vs_production": null
  },
  {
    "step": 1,
    "added": "replay",
    "modules": [
      "production",
      "replay"
    ],
    "metrics": {
      "n": 19160,
      "ev": -6494.9805,
      "mean_ev": -0.338986,
      "pf": 0.4966,
      "wr": 0.1323,
      "mutual_information": 0.155965,
      "information_gain": 0.155965,
      "precision": 0.1322,
      "recall": 0.29,
      "f1": 0.1816
    },
    "delta_vs_prev": {
      "delta_ev": 1264.7133,
      "delta_pf": 0.1229,
      "delta_wr": -0.0003,
      "delta_mi": 0.000689,
      "delta_ig": 0.000689,
      "delta_f1": 0.0013
    },
    "delta_vs_production": {
      "delta_ev": 1264.7133,
      "delta_pf": 0.1229,
      "delta_wr": -0.0003,
      "delta_mi": 0.000689,
      "delta_ig": 0.000689,
      "delta_f1": 0.0013
    }
  },
  {
    "step": 2,
    "added": "edge",
    "modules": [
      "production",
      "replay",
      "edge"
    ],
    "metrics": {
      "n": 19160,
      "ev": 4399.7958,
      "mean_ev": 0.229634,
      "pf": 20.1387,
      "wr": 0.306,
      "mutual_information": 0.000881,
      "information_gain": 0.000881,
      "precision": 0.2611,
      "recall": 0.2875,
      "f1": 0.2737
    },
    "delta_vs_prev": {
      "delta_ev": 10894.7763,
      "delta_pf": 19.6421,
      "delta_wr": 0.1737,
      "delta_mi": -0.155084,
      "delta_ig": -0.155084,
      "delta_f1": 0.0921
    },
    "delta_vs_production": {
      "delta_ev": 12159.4896,
      "delta_pf": 19.765,
      "delta_wr": 0.1734,
      "delta_mi": -0.154395,
      "delta_ig": -0.154395,
      "delta_f1": 0.0934
    }
  },
  {
    "step": 3,
    "added": "alpha",
    "modules": [
      "production",
      "replay",
      "edge",
      "alpha"
    ],
    "metrics": {
      "n": 19160,
      "ev": 4399.7958,
      "mean_ev": 0.229634,
      "pf": 20.1387,
      "wr": 0.306,
      "mutual_information": 0.000881,
      "information_gain": 0.000881,
      "precision": 0.2611,
      "recall": 0.2875,
      "f1": 0.2737
    },
    "delta_vs_prev": {
      "delta_ev": 0.0,
      "delta_pf": 0.0,
      "delta_wr": 0.0,
      "delta_mi": 0.0,
      "delta_ig": 0.0,
      "delta_f1": 0.0
    },
    "delta_vs_production": {
      "delta_ev": 12159.4896,
      "delta_pf": 19.765,
      "delta_wr": 0.1734,
      "delta_mi": -0.154395,
      "delta_ig": -0.154395,
      "delta_f1": 0.0934
    }
  },
  {
    "step": 4,
    "added": "causality",
    "modules": [
      "production",
      "replay",
      "edge",
      "alpha",
      "causality"
    ],
    "metrics": {
      "n": 19160,
      "ev": 19552.3035,
      "mean_ev": 1.020475,
      "pf": null,
      "wr": 1.0,
      "mutual_information": 0.0,
      "information_gain": 0.0,
      "precision": 1.0,
      "recall": 0.9998,
      "f1": 0.9999
    },
    "delta_vs_prev": {
      "delta_ev": 15152.5077,
      "delta_pf": null,
      "delta_wr": 0.694,
      "delta_mi": -0.000881,
      "delta_ig": -0.000881,
      "delta_f1": 0.7262
    },
    "delta_vs_production": {
      "delta_ev": 27311.9973,
      "delta_pf": null,
      "delta_wr": 0.8674,
      "delta_mi": -0.155276,
      "delta_ig": -0.155276,
      "delta_f1": 0.8196
    }
  },
  {
    "step": 5,
    "added": "brain",
    "modules": [
      "production",
      "replay",
      "edge",
      "alpha",
      "causality",
      "brain"
    ],
    "metrics": {
      "n": 19160,
      "ev": 6879.8549,
      "mean_ev": 0.359074,
      "pf": null,
      "wr": 0.6767,
      "mutual_information": 0.514227,
      "information_gain": 0.514227,
      "precision": 1.0,
      "recall": 0.2906,
      "f1": 0.4503
    },
    "delta_vs_prev": {
      "delta_ev": -12672.4486,
      "delta_pf": null,
      "delta_wr": -0.3233,
      "delta_mi": 0.514227,
      "delta_ig": 0.514227,
      "delta_f1": -0.5496
    },
    "delta_vs_production": {
      "delta_ev": 14639.5487,
      "delta_pf": null,
      "delta_wr": 0.5441,
      "delta_mi": 0.358951,
      "delta_ig": 0.358951,
      "delta_f1": 0.27
    }
  },
  {
    "step": 6,
    "added": "evolution",
    "modules": [
      "production",
      "replay",
      "edge",
      "alpha",
      "causality",
      "brain",
      "evolution"
    ],
    "metrics": {
      "n": 19160,
      "ev": 2492.7768,
      "mean_ev": 0.130103,
      "pf": null,
      "wr": 0.5459,
      "mutual_information": 0.991892,
      "information_gain": 0.991892,
      "precision": 1.0,
      "recall": 0.0034,
      "f1": 0.0068
    },
    "delta_vs_prev": {
      "delta_ev": -4387.0781,
      "delta_pf": null,
      "delta_wr": -0.1308,
      "delta_mi": 0.477665,
      "delta_ig": 0.477665,
      "delta_f1": -0.4435
    },
    "delta_vs_production": {
      "delta_ev": 10252.4706,
      "delta_pf": null,
      "delta_wr": 0.4133,
      "delta_mi": 0.836616,
      "delta_ig": 0.836616,
      "delta_f1": -0.1735
    }
  }
]
```
