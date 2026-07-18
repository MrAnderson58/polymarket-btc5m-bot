"""Portfolio Advice — template AI (no Claude / GPT)."""

from __future__ import annotations

from bot.terminal.portfolio.models import ExposureSlice, PortfolioAdvice, PositionWeight


def build_advice(
    *,
    crypto_pct: float,
    cash_pct: float | None,
    expected_dd_pct: float | None,
    weights: tuple[PositionWeight, ...],
    sectors: tuple[ExposureSlice, ...],
) -> PortfolioAdvice:
    bullets: list[str] = []
    actions: list[str] = []

    # Dominant crypto concentration — match product example
    if crypto_pct >= 60:
        bullets.append(f"У вас {crypto_pct:.0f}% Crypto")
        btc = next((w for w in weights if w.symbol == "BTC"), None)
        if btc is not None and btc.weight_pct >= 20:
            actions.append("Лучше сократить BTC.")
        else:
            actions.append("Лучше сократить crypto-риск.")
        has_gold = any(w.symbol == "GOLD" or w.sector == "Commodities" for w in weights)
        if not has_gold:
            actions.append("Добавить Gold.")
        else:
            actions.append("Увеличить долю Gold / commodities.")

    # Cash buffer
    if cash_pct is not None:
        if cash_pct < 15:
            bullets.append(f"Cash только {cash_pct:.0f}% — мало буфера")
            actions.append("Поднять Cash выше 20%.")
        elif cash_pct >= 50 and crypto_pct < 30:
            bullets.append(f"Cash {cash_pct:.0f}% — портфель оборонительный")

    # Single-name risk
    if weights:
        top = weights[0]
        if top.weight_pct >= 40:
            bullets.append(f"{top.symbol} = {top.weight_pct:.0f}% — концентрация")
            actions.append(f"Урезать {top.symbol} ниже 30%.")

    # DD
    if expected_dd_pct is not None and expected_dd_pct >= 15:
        bullets.append(f"Expected DD ≈ {expected_dd_pct:.0f}%")
        actions.append("Снизить Open Risk до остывания волатильности.")

    # Diversification positive
    if crypto_pct < 40 and len(sectors) >= 3 and not actions:
        bullets.append("Экспозиция относительно сбалансирована")
        actions.append("Держать дисциплину размера позиции.")

    if not bullets:
        bullets.append("Недостаточно открытого риска для сильных выводов")
    if not actions:
        actions.append("Мониторить корреляцию crypto и макро-события.")

    # Headline mirrors user example
    if crypto_pct >= 60:
        headline = f"AI · Crypto {crypto_pct:.0f}%"
    elif expected_dd_pct and expected_dd_pct >= 15:
        headline = f"AI · Expected DD {expected_dd_pct:.0f}%"
    else:
        headline = "AI · Portfolio Advice"

    return PortfolioAdvice(
        headline=headline,
        bullets=tuple(bullets),
        actions=tuple(dict.fromkeys(actions)),  # stable unique
    )


def format_ai_summary(advice: PortfolioAdvice) -> str:
    lines = [advice.headline, ""]
    if advice.bullets:
        lines.append("Portfolio")
        lines.extend(advice.bullets)
        lines.append("")
    lines.append("Portfolio Advice")
    lines.extend(advice.actions)
    return "\n".join(lines).rstrip()


__all__ = ["build_advice", "format_ai_summary"]
