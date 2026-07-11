"""Phase F.2 Task H — AI analyst v2 (human explanation from JSON, no decisions)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.exchange_consensus_f2 import CONSENSUS_STRONG, CONSENSUS_WEAK
from bot.research.market_events.signal_intelligence.market_structure_f2 import LABEL_SWEEP

PROMPT_VERSION_F2 = "f2_analyst_v2"


@dataclass(frozen=True)
class AiAnalystV2Result:
    summary_ru: str
    input_json: dict[str, Any]


def build_ai_input_json(
    *,
    symbol: str,
    shock_return_pct: float,
    exchange_consensus: str,
    funding_regime: str,
    oi_regime: str,
    volume_label: str,
    atr_percentile: float,
    structure_labels: list[str],
    correlation_verdict: str,
    correlation_peers: list[dict[str, Any]],
    historical_count: int,
    historical_reversal_rate: float,
    news_count: int,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "shock_return_pct": shock_return_pct,
        "exchange_consensus": exchange_consensus,
        "funding_regime": funding_regime,
        "oi_regime": oi_regime,
        "volume_label": volume_label,
        "atr_percentile": atr_percentile,
        "structure_labels": structure_labels,
        "correlation_verdict": correlation_verdict,
        "correlation_peers": correlation_peers,
        "historical_count": historical_count,
        "historical_reversal_rate": historical_reversal_rate,
        "news_count": news_count,
    }


def analyze_ai_v2(payload: dict[str, Any]) -> AiAnalystV2Result:
    """Deterministic narrative — LLM would receive same JSON; no trading decisions."""
    lines: list[str] = []
    labels = payload.get("structure_labels") or []

    if LABEL_SWEEP in labels:
        lines.append("Импульс выглядит как снятие ликвидности.")
    elif abs(float(payload.get("shock_return_pct") or 0)) >= 5:
        lines.append("Импульс резкий — возможен перегрев.")
    else:
        lines.append("Импульс умеренный, без явного экстремума.")

    if int(payload.get("news_count") or 0) == 0:
        lines.append("Фундаментальных новостей нет.")
    else:
        lines.append("Есть новостной контекст — проверьте катализатор.")

    consensus = payload.get("exchange_consensus", "")
    if consensus == CONSENSUS_STRONG:
        lines.append("Движение подтверждается всеми биржами.")
    elif consensus == CONSENSUS_WEAK:
        lines.append("Движение не подтверждается всем рынком.")
        lines.append("Вероятность ложного пробоя повышена.")
    else:
        lines.append("Частичное подтверждение по биржам.")

    if payload.get("correlation_verdict") == "CORRELATION_BROKEN":
        lines.append("Корреляция с рынком нарушена — idiosyncratic move.")
    elif payload.get("correlation_verdict") == "MARKET_CONFIRMED":
        lines.append("Движение подтверждается смежными активами.")

    fr = payload.get("funding_regime", "")
    oi = payload.get("oi_regime", "")
    if fr == "accelerating":
        lines.append("Funding ускоряется — crowded side.")
    if oi == "divergence":
        lines.append("Funding/OI дивергенция — осторожно с continuation.")

    hr = float(payload.get("historical_reversal_rate") or 0.5)
    if hr >= 0.65:
        lines.append(f"История: {int(hr * 100)}% похожих кейсов закончились откатом.")
    elif hr <= 0.35:
        lines.append("История слабо поддерживает fade-сценарий.")

    summary = "\n".join(lines[:6])
    return AiAnalystV2Result(summary_ru=summary, input_json=payload)
