"""Score explainability (V7.1.3)."""

from __future__ import annotations

from dataclasses import dataclass

from bot.terminal.scanner.models import RankComponents, ScannerResult
from bot.terminal.scanner.ranking import WEIGHTS, _clamp, compute_score


@dataclass(frozen=True)
class ScoreContribution:
    name: str
    points: float
    raw: float | None = None


@dataclass(frozen=True)
class ScoreExplanation:
    symbol: str
    score: float
    contributions: tuple[ScoreContribution, ...]
    direction: str = "—"

    def format_text(self) -> str:
        lines = [
            f"Score {self.score:.0f}",
            "",
            f"Почему {self.score:.0f}?",
            "",
        ]
        for c in self.contributions:
            sign = "+" if c.points >= 0 else ""
            lines.append(f"{c.name:<12}{sign}{c.points:.0f}")
        return "\n".join(lines)


_DISPLAY_NAMES = {
    "trend": "Trend",
    "learning": "Learning",
    "pattern": "Pattern",
    "volume": "Volume",
    "news": "News",
    "confidence": "Confidence",
    "ai": "AI",
    "risk": "Risk",
}


def explain_score(
    components: RankComponents,
    *,
    symbol: str = "",
    direction: str = "—",
) -> ScoreExplanation:
    """Break score into weighted point contributions that sum ≈ score."""
    values: dict[str, float] = {}
    mapping = {
        "confidence": components.confidence,
        "ai": components.ai,
        "learning": components.learning,
        "trend": components.trend,
        "volume": components.volume,
        "news": components.news,
        "pattern": components.pattern,
    }
    for key, raw in mapping.items():
        clamped = _clamp(raw)
        if clamped is not None:
            values[key] = clamped

    score = compute_score(components)
    if not values:
        return ScoreExplanation(symbol=symbol, score=score, contributions=(), direction=direction)

    # points_i = score * (w_i * v_i) / sum(w_j * v_j)
    denom = sum(WEIGHTS[k] * values[k] for k in values) or 1.0
    contribs: list[ScoreContribution] = []
    for key, val in sorted(values.items(), key=lambda kv: WEIGHTS[kv[0]] * kv[1], reverse=True):
        points = round(score * (WEIGHTS[key] * val) / denom, 1)
        contribs.append(
            ScoreContribution(
                name=_DISPLAY_NAMES.get(key, key.title()),
                points=points,
                raw=val,
            )
        )

    used = sum(c.points for c in contribs)
    residual = round(score - used, 1)
    if abs(residual) >= 0.5:
        contribs.append(ScoreContribution(name="Risk", points=residual, raw=None))

    return ScoreExplanation(
        symbol=symbol,
        score=score,
        contributions=tuple(contribs),
        direction=direction,
    )


def explain_scanner_result(scan: ScannerResult) -> ScoreExplanation:
    return explain_score(
        scan.components,
        symbol=scan.symbol,
        direction=scan.direction or "—",
    )


__all__ = [
    "ScoreContribution",
    "ScoreExplanation",
    "explain_scanner_result",
    "explain_score",
]
