"""Explain + template AI summary for DecisionCard (no Claude / GPT)."""

from __future__ import annotations

from bot.terminal.decision.models import LearningHint
from bot.terminal.decision.scoring import component_strength, risk_budget_pct
from bot.terminal.instruments.profiles import MarketProfile
from bot.terminal.models.dto import SignalCard
from bot.terminal.scanner.models import ScannerResult


def _dir_label(direction: str) -> str:
    d = (direction or "").upper()
    if d in {"LONG", "SHORT"}:
        return d
    return d or "—"


def build_reasons(
    scan: ScannerResult,
    profile: MarketProfile | None,
    learning: LearningHint | None,
    signal: SignalCard | None,
) -> tuple[str, ...]:
    """Human 'Почему' bullets with ✓ prefix."""
    c = scan.components
    direction = _dir_label(scan.direction)
    out: list[str] = []

    if component_strength(c, "trend", 55):
        if direction == "SHORT":
            out.append("✓ сильный нисходящий тренд")
        elif direction == "LONG":
            out.append("✓ сильный восходящий тренд")
        else:
            out.append("✓ выраженный тренд")

    if profile and profile.has_funding and (c.ai or 0) >= 50:
        out.append("✓ funding перекошен" if direction == "SHORT" else "✓ funding в пользу идеи")

    if component_strength(c, "volume", 50):
        out.append("✓ volume выше среднего")

    if c.pattern is not None and float(c.pattern) >= 50:
        # Map 0–100 pattern strength → display id bucket
        pid = max(1, min(99, int(float(c.pattern) // 5) or 1))
        out.append(f"✓ pattern #{pid}")
    elif any("pattern" in r.lower() for r in scan.reasons):
        out.append("✓ pattern подтверждён сканером")

    if learning and learning.confirms:
        out.append("✓ learning подтверждает")
    elif c.learning is not None and float(c.learning) >= 55:
        out.append("✓ learning подтверждает")

    if signal and signal.summary and signal.status not in {"unavailable", "unknown"}:
        out.append(f"✓ сигнал: {signal.status}")

    for r in scan.reasons:
        text = str(r).strip()
        if not text:
            continue
        bullet = text if text.startswith("✓") else f"✓ {text}"
        if bullet not in out and len(out) < 8:
            out.append(bullet)

    if not out:
        out.append(f"✓ scanner score {scan.score:.0f}")
    return tuple(out)


def build_warnings(
    scan: ScannerResult,
    profile: MarketProfile | None,
    learning: LearningHint | None,
) -> tuple[str, ...]:
    """⚠ bullets — profile / macro / weak confirmation."""
    out: list[str] = []
    c = scan.components

    if profile and profile.macro_sensitive:
        out.append("⚠ макро-чувствительный рынок")
    if profile and profile.has_funding:
        out.append("⚠ funding может развернуться")
    if profile and profile.uses_liquidations:
        out.append("⚠ риск каскадных ликвидаций")
    if profile and profile.uses_earnings:
        out.append("⚠ возможен earnings-gap")
    if learning and learning.confirms is False:
        out.append("⚠ learning не подтверждает")
    if c.news is not None and float(c.news) < 40:
        out.append("⚠ news-фон слабый / шумный")
    if scan.score < 70:
        out.append("⚠ score ниже сильного порога")

    # Keep list short & distinctive
    dedup: list[str] = []
    for w in out:
        if w not in dedup:
            dedup.append(w)
    return tuple(dedup[:5])


def holding_time_for(profile: MarketProfile | None, score: float) -> str:
    if profile and profile.is_24_7:
        if score >= 85:
            return "4–24h (crypto swing)"
        return "1–8h (intraday)"
    if profile and not profile.is_24_7:
        return "RTH session / overnight gap risk"
    return "intraday"


def template_ai_summary(
    *,
    symbol: str,
    direction: str,
    score: float,
    confidence: float | None,
    reasons: tuple[str, ...],
    warnings: tuple[str, ...],
    risk: str,
    holding_time: str,
) -> str:
    """Deterministic template — no Claude / GPT."""
    d = _dir_label(direction)
    conf = confidence if confidence is not None else score
    if score >= 85:
        cont = "Вероятность продолжения движения выше средней."
    elif score >= 70:
        cont = "Вероятность продолжения движения около средней."
    else:
        cont = "Вероятность продолжения движения ниже средней — идея слабее."

    risk_lines = list(warnings) if warnings else ["существенных предупреждений нет"]
    risk_block = "\n".join(f"• {w.lstrip('⚠ ').strip()}" for w in risk_lines)

    why = "\n".join(reasons[:5]) if reasons else "• недостаточно факторов"

    return (
        f"{symbol} {d}\n\n"
        f"{cont}\n\n"
        f"Почему\n{why}\n\n"
        f"Основные риски:\n{risk_block}\n\n"
        f"Рекомендуемый риск:\n{risk}\n"
        f"Горизонт: {holding_time}\n"
        f"Уверенность: {conf:.0f}/100 · Score: {score:.0f}"
    )


__all__ = [
    "build_reasons",
    "build_warnings",
    "holding_time_for",
    "template_ai_summary",
]
