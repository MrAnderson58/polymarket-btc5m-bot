"""S43 — fast similarity / clustering helpers for news articles."""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Iterable

_STOP = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "as", "at",
    "by", "from", "with", "after", "before", "over", "under", "into", "is",
    "are", "was", "were", "be", "been", "its", "it", "this", "that", "new",
    "says", "say", "amid", "as", "vs", "via",
})


def normalize_text(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^\w\s+-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def significant_tokens(text: str, *, min_len: int = 3) -> set[str]:
    tokens = set()
    for tok in normalize_text(text).split():
        if len(tok) < min_len or tok in _STOP:
            continue
        tokens.add(tok)
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def title_similarity(a: str, b: str) -> float:
    na = normalize_text(a)
    nb = normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def symbol_overlap(a: Iterable[str], b: Iterable[str]) -> float:
    sa = {str(x).upper() for x in a if x}
    sb = {str(x).upper() for x in b if x}
    if not sa and not sb:
        return 0.35  # both macro-ish
    if not sa or not sb:
        return 0.15
    return jaccard(sa, sb)


def time_proximity(ts_a: int, ts_b: int, *, window_sec: int = 6 * 3600) -> float:
    gap = abs(int(ts_a) - int(ts_b))
    if gap <= 900:
        return 1.0
    if gap >= window_sec:
        return 0.0
    return max(0.0, 1.0 - (gap / float(window_sec)))


def entity_overlap(a: set[str], b: set[str]) -> float:
    return jaccard(a, b)


def article_similarity(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    time_window_sec: int = 6 * 3600,
) -> float:
    """Weighted similarity across title, summary tokens, symbols, entities, time."""
    t_sim = title_similarity(left.get("title") or "", right.get("title") or "")
    tok_sim = jaccard(
        left.get("tokens") or set(),
        right.get("tokens") or set(),
    )
    sym_sim = symbol_overlap(left.get("symbols") or [], right.get("symbols") or [])
    ent_sim = entity_overlap(
        left.get("entities") or set(),
        right.get("entities") or set(),
    )
    tim_sim = time_proximity(
        int(left.get("timestamp") or 0),
        int(right.get("timestamp") or 0),
        window_sec=time_window_sec,
    )
    # Shared narrative labels are a strong merge signal (ETF↔ETF, Fed↔Fed).
    narr_l = set(left.get("narratives") or [])
    narr_r = set(right.get("narratives") or [])
    narr_sim = jaccard(narr_l, narr_r) if (narr_l or narr_r) else 0.0

    score = (
        0.28 * t_sim
        + 0.20 * tok_sim
        + 0.18 * sym_sim
        + 0.12 * ent_sim
        + 0.10 * tim_sim
        + 0.12 * narr_sim
    )
    # Boost when both mention the same core theme tokens.
    theme_hits = {"etf", "fed", "fomc", "hack", "hyperliquid", "inflow", "inflows"}
    lt = left.get("tokens") or set()
    rt = right.get("tokens") or set()
    shared_theme = theme_hits & lt & rt
    if shared_theme and (sym_sim >= 0.34 or (not (left.get("symbols") or right.get("symbols")))):
        score = max(score, 0.55)
    # Hard gate: if no token/title/narrative signal and no symbol overlap, reject.
    if t_sim < 0.30 and tok_sim < 0.20 and sym_sim < 0.34 and narr_sim < 0.34:
        score *= 0.35
    return score


def cluster_key_bucket(article: dict[str, Any]) -> str:
    """Coarse bucket to keep pairwise comparisons small (perf)."""
    syms = sorted({str(s).upper() for s in (article.get("symbols") or []) if s})[:1]
    narr = [n for n in (article.get("narratives") or []) if n and n != "General"]
    primary = narr[0] if narr else "GEN"
    # Same-day bucket so thematic coverage stays mergeable.
    ts = int(article.get("timestamp") or 0)
    bucket = ts // (24 * 3600) if ts else 0
    toks = article.get("tokens") or set()
    for tip in ("etf", "fed", "fomc", "hack", "whale", "sec", "cpi", "ppi"):
        if tip in toks:
            if not syms:
                return f"{primary}|MACRO|{tip}|{bucket}"
            return f"{primary}|{syms[0]}|{tip}|{bucket}"
    if not syms:
        return f"{primary}|MACRO|{bucket}"
    return f"{primary}|{syms[0]}|{bucket}"


def event_uid_from_seed(title: str, symbols: list[str], first_seen: int) -> str:
    # Prefer stable thematic uid (symbols + hour), title only as weak salt.
    theme = normalize_text(title)
    tips = []
    for tip in ("etf", "fed", "hack", "hyperliquid", "layer2", "defi", "whale"):
        if tip in theme:
            tips.append(tip)
    tip_key = "-".join(tips[:2]) or theme.split(" ")[:4]
    if isinstance(tip_key, list):
        tip_key = "-".join(tip_key)
    raw = f"{tip_key}|{','.join(sorted(symbols))}|{first_seen // 3600}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
