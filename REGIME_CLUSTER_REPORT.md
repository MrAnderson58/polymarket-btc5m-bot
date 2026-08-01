# REGIME_CLUSTER_REPORT

Automatic market-state clustering (KMeans + optional HDBSCAN; PCA/UMAP embed).

- ok: True
- method: `kmeans_k=3_sil=0.983`
- hdbscan: hdbscan_unavailable
- embedding: pca2
- n_regimes: 3
- features_used: atr_pct, oi_delta

## Regimes

### auto_regime_0 (n=24, share=0.48)

- mean_pnl=-25.1221 winrate=29.17%
- top features:
  - `oi_delta` z=-1.0
  - `atr_pct` z=-0.204

### auto_regime_1 (n=24, share=0.48)

- mean_pnl=-29.9669 winrate=37.5%
- top features:
  - `oi_delta` z=1.0
  - `atr_pct` z=-0.204

### auto_regime_2 (n=2, share=0.04)

- mean_pnl=28.7115 winrate=100.0%
- top features:
  - `atr_pct` z=4.899
  - `oi_delta` z=-0.001
