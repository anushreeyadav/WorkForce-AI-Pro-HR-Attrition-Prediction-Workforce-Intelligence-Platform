"""Risk scoring and explanations for one validated feature record.

The score is the model's estimated probability x 100. It is a relative risk estimate from a statistical
model, not a confidence level and not proof that anyone will leave. Explanations describe which inputs
pushed this estimate up or down relative to the training data; they are associations, not causes.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from sklearn.pipeline import Pipeline

from ml.attrition.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS, missing_inputs, validate_record

LIMITATIONS = (
    "Statistical estimate for HR decision support only; not a prediction of any individual's decision.",
    "Associations in historical data are not causes.",
    "Aggregate evaluation metrics do not guarantee accuracy for an individual.",
    "Must not be used on its own for any employment decision.",
)


@dataclass(frozen=True)
class Thresholds:
    medium: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.medium < self.high <= Decimal(100)):
            raise ValueError("Thresholds must satisfy 0 < medium < high <= 100")


@dataclass(frozen=True)
class PredictionOutcome:
    risk_score: Decimal
    risk_category: str
    factors: list[dict]
    missing_inputs: list[str]


def score_from_probability(probability: float) -> Decimal:
    clipped = min(max(float(probability), 0.0), 1.0)
    return (Decimal(str(clipped)) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def categorize(score: Decimal, thresholds: Thresholds) -> str:
    if score >= thresholds.high:
        return "HIGH"
    if score >= thresholds.medium:
        return "MEDIUM"
    return "LOW"


def _base_feature(transformed_name: str) -> str:
    """Map a ColumnTransformer output name (e.g. 'num__missingindicator_late_rate_90d',
    'cat__department_7') back to its schema feature."""
    name = transformed_name.split("__", 1)[-1].removeprefix("missingindicator_")
    for categorical in CATEGORICAL_FEATURES:
        if name == categorical or name.startswith(categorical + "_"):
            return categorical
    return name


def explain(pipeline: Pipeline, row, exclude: frozenset[str] = frozenset(), top_n: int = 3) -> list[dict]:
    """Top contributions to the log-odds. Features in `exclude` (missing inputs, which were imputed)
    are left out so an absent value is never presented as a reason."""
    preprocess, classifier = pipeline[0], pipeline[-1]
    transformed = np.asarray(preprocess.transform(row))[0]
    contributions = transformed * classifier.coef_[0]
    totals: dict[str, float] = {}
    for name, value in zip(preprocess.get_feature_names_out(), contributions):
        feature = _base_feature(name)
        totals[feature] = totals.get(feature, 0.0) + float(value)
    ranked = sorted(((f, v) for f, v in totals.items()
                     if f in FEATURE_COLUMNS and f not in exclude and abs(v) > 1e-9),
                    key=lambda item: abs(item[1]), reverse=True)[:top_n]
    return [
        {"feature": feature, "direction": "raises" if value > 0 else "lowers",
         "weight": round(abs(value), 4)}
        for feature, value in ranked
    ]


def predict(pipeline: Pipeline, record: dict, thresholds: Thresholds) -> PredictionOutcome:
    row = validate_record(record)
    probability = float(pipeline.predict_proba(row[FEATURE_COLUMNS])[0, 1])
    score = score_from_probability(probability)
    missing = missing_inputs(record)
    return PredictionOutcome(
        risk_score=score,
        risk_category=categorize(score, thresholds),
        factors=explain(pipeline, row[FEATURE_COLUMNS], exclude=frozenset(missing)),
        missing_inputs=missing,
    )
