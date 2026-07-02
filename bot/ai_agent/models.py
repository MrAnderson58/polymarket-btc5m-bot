"""ML model interfaces — prepared but not used in live/observe v1."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from bot.ai_agent.decision import compute_ai_score
from bot.ai_agent.features import feature_vector

FEATURE_NAMES = (
    "entry_price",
    "seconds_open",
    "spread",
    "distance_to_strike",
    "btc_move_5s",
    "btc_move_10s",
    "btc_move_15s",
    "btc_move_20s",
    "btc_move_30s",
    "btc_move_45s",
    "btc_move_60s",
    "btc_move_90s",
    "volatility_15s",
    "volatility_30s",
    "volatility_60s",
    "mfe",
    "mae",
    "holding_time",
)


class BasePredictor(ABC):
    """Interface for sklearn / XGBoost / LightGBM predictors."""

    name: str = "base"

    @abstractmethod
    def fit(self, rows: list[dict[str, Any]], *, target: str = "is_win") -> None:
        raise NotImplementedError

    @abstractmethod
    def predict_score(self, features: dict[str, Any]) -> float:
        """Return 0–100 score."""
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


class RuleBasedPredictor(BasePredictor):
    name = "rule_based"

    def fit(self, rows: list[dict[str, Any]], *, target: str = "is_win") -> None:
        del rows, target

    def predict_score(self, features: dict[str, Any]) -> float:
        return compute_ai_score(features)


class RandomForestPredictor(BasePredictor):
    name = "random_forest"

    def __init__(self) -> None:
        self._model = None

    def is_available(self) -> bool:
        try:
            import sklearn  # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, rows: list[dict[str, Any]], *, target: str = "is_win") -> None:
        if not self.is_available() or len(rows) < 30:
            return
        from sklearn.ensemble import RandomForestClassifier

        xs, ys = _xy(rows, target=target)
        if not xs:
            return
        self._model = RandomForestClassifier(n_estimators=100, random_state=42)
        self._model.fit(xs, ys)

    def predict_score(self, features: dict[str, Any]) -> float:
        if self._model is None:
            return compute_ai_score(features)
        vec = _vectorize(features)
        proba = self._model.predict_proba([vec])[0]
        return round(float(max(proba)) * 100, 1)


class XGBoostPredictor(BasePredictor):
    name = "xgboost"

    def __init__(self) -> None:
        self._model = None

    def is_available(self) -> bool:
        try:
            import xgboost  # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, rows: list[dict[str, Any]], *, target: str = "is_win") -> None:
        if not self.is_available() or len(rows) < 30:
            return
        import xgboost as xgb

        xs, ys = _xy(rows, target=target)
        if not xs:
            return
        self._model = xgb.XGBClassifier(
            n_estimators=80,
            max_depth=4,
            random_state=42,
            eval_metric="logloss",
        )
        self._model.fit(xs, ys)

    def predict_score(self, features: dict[str, Any]) -> float:
        if self._model is None:
            return compute_ai_score(features)
        vec = _vectorize(features)
        proba = self._model.predict_proba([vec])[0]
        return round(float(max(proba)) * 100, 1)


class LightGBMPredictor(BasePredictor):
    name = "lightgbm"

    def __init__(self) -> None:
        self._model = None

    def is_available(self) -> bool:
        try:
            import lightgbm  # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, rows: list[dict[str, Any]], *, target: str = "is_win") -> None:
        if not self.is_available() or len(rows) < 30:
            return
        import lightgbm as lgb

        xs, ys = _xy(rows, target=target)
        if not xs:
            return
        self._model = lgb.LGBMClassifier(
            n_estimators=80,
            max_depth=4,
            random_state=42,
            verbose=-1,
        )
        self._model.fit(xs, ys)

    def predict_score(self, features: dict[str, Any]) -> float:
        if self._model is None:
            return compute_ai_score(features)
        vec = _vectorize(features)
        proba = self._model.predict_proba([vec])[0]
        return round(float(max(proba)) * 100, 1)


def _vectorize(features: dict[str, Any]) -> list[float]:
    vec = feature_vector(features)
    out: list[float] = []
    for name in FEATURE_NAMES:
        v = vec.get(name)
        out.append(float(v) if v is not None else 0.0)
    return out


def _xy(rows: list[dict[str, Any]], *, target: str) -> tuple[list[list[float]], list[int]]:
    xs: list[list[float]] = []
    ys: list[int] = []
    for row in rows:
        if target == "is_win":
            y = 1 if (row.get("pnl") or 0) > 0 else 0
        else:
            y = 1 if row.get("decision") == "ALLOW" else 0
        xs.append(_vectorize(row))
        ys.append(y)
    return xs, ys


def get_predictor(name: str = "rule_based") -> BasePredictor:
    registry: dict[str, BasePredictor] = {
        "rule_based": RuleBasedPredictor(),
        "random_forest": RandomForestPredictor(),
        "xgboost": XGBoostPredictor(),
        "lightgbm": LightGBMPredictor(),
    }
    return registry.get(name, RuleBasedPredictor())


def list_models() -> list[dict[str, Any]]:
    return [
        {"name": "rule_based", "available": True, "active_in_v1": True},
        {"name": "random_forest", "available": RandomForestPredictor().is_available(), "active_in_v1": False},
        {"name": "xgboost", "available": XGBoostPredictor().is_available(), "active_in_v1": False},
        {"name": "lightgbm", "available": LightGBMPredictor().is_available(), "active_in_v1": False},
    ]
