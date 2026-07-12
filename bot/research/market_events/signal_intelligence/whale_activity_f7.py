"""Phase F.7 Task D — whale activity detection via volume/OI spikes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles

WHALE_VOLUME_SPIKE = 3.5
WHALE_OI_CHANGE = 2.0


@dataclass(frozen=True)
class WhaleActivityF7:
    score: float
    large_trades: bool
    oi_surge: bool
    volume_spike: bool
    cluster_detected: bool
    events_in_cluster: int
    summary: str


def _volume_zscore(volumes: list[float]) -> float:
    if len(volumes) < 5:
        return 0.0
    recent = volumes[-1]
    baseline = volumes[:-1]
    mean = sum(baseline) / len(baseline)
    if mean <= 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in baseline) / len(baseline)
    std = variance ** 0.5
    if std <= 0:
        return 0.0
    return (recent - mean) / std


def analyze_whale_activity(conn: Any, *, symbol: str, event_ts: int) -> WhaleActivityF7:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=48)
    volumes = [b.volume for b in bars] if bars else []
    vol_z = _volume_zscore(volumes) if volumes else 0.0
    volume_spike = vol_z >= 2.5

    oi_row = conn.execute(
        """
        SELECT c.open_interest FROM market_event_exchange_context c
        JOIN market_events e ON e.id = c.event_id
        WHERE e.symbol = ? AND c.open_interest IS NOT NULL
        ORDER BY c.created_at DESC LIMIT 2
        """,
        (symbol,),
    ).fetchall()
    oi_surge = False
    if len(oi_row) >= 2:
        prev, cur = float(oi_row[1]["open_interest"]), float(oi_row[0]["open_interest"])
        if prev > 0 and abs(cur - prev) / prev * 100 >= WHALE_OI_CHANGE:
            oi_surge = True

    since = event_ts - 3600
    cluster_count = conn.execute(
        """
        SELECT COUNT(*) FROM market_events
        WHERE symbol = ? AND event_ts >= ? AND ABS(return_pct) >= 2.0
        """,
        (symbol, since),
    ).fetchone()[0]
    cluster_detected = cluster_count >= 2

    large_trades = volume_spike and vol_z >= 3.0
    score = 0.0
    if large_trades:
        score += 35
    if oi_surge:
        score += 30
    if volume_spike:
        score += 20
    if cluster_detected:
        score += 15
    score = min(100.0, score)

    parts: list[str] = []
    if large_trades:
        parts.append("крупные сделки")
    if oi_surge:
        parts.append("резкое изменение OI")
    if volume_spike:
        parts.append("всплеск объёма")
    if cluster_detected:
        parts.append(f"кластер ({cluster_count} событий)")
    summary = ", ".join(parts) if parts else "активность китов не выражена"

    return WhaleActivityF7(
        score=round(score, 1),
        large_trades=large_trades,
        oi_surge=oi_surge,
        volume_spike=volume_spike,
        cluster_detected=cluster_detected,
        events_in_cluster=int(cluster_count),
        summary=summary,
    )
