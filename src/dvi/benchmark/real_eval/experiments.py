"""Two experiments over real data, both through the confidence-attaching path.

Every fired symptom carries a calibrated ``confidence`` (``detect_symptoms`` is
called with the default model), so the same runs that measure recall and the
false-positive rate also yield the ``(confidence, label)`` pairs the calibration
report needs — no second pass, no re-fit.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import polars as pl

from dvi.calibration.loader import load_model
from dvi.calibration.model import LogisticModel
from dvi.detection import DEFAULT_DISTRIBUTION_THRESHOLD
from dvi.pipeline.analyze import detect_symptoms

from .._sampling import two_sample_splits

Pair = tuple[float, int]


@dataclass(frozen=True)
class RealFpReport:
    """Real-vs-real specificity result plus its (confidence, 0) calibration pairs."""

    trials: int
    checks: int
    fires: int
    examples: list[str] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)

    @property
    def false_positive_rate(self) -> float:
        return self.fires / self.checks if self.checks else 0.0


@dataclass(frozen=True)
class RealRecallReport:
    """Injected-recall result plus its (confidence, 1) calibration pairs."""

    family: str
    column: str
    trials: int
    hits: int
    pairs: list[Pair] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.hits / self.trials if self.trials else 0.0


def _resolve_model(model: LogisticModel | None) -> LogisticModel:
    return model if model is not None else load_model()


def real_vs_real_report(
    df: pl.DataFrame,
    *,
    columns: list[str],
    n: int,
    trials: int,
    seed: int = 0,
    dist_threshold: float = DEFAULT_DISTRIBUTION_THRESHOLD,
    model: LogisticModel | None = None,
) -> RealFpReport:
    """Run every detector over disjoint same-distribution splits; any firing is an FP.

    Each (split, column) is one check. A fired check contributes a
    ``(max confidence over its symptoms, 0)`` pair; a silent check contributes
    ``(0.0, 0)`` — both are ground-truth negatives.
    """
    model = _resolve_model(model)
    checks = 0
    fires = 0
    examples: list[str] = []
    pairs: list[Pair] = []
    for baseline, current in two_sample_splits(df, n, trials, seed=seed):
        for col in columns:
            checks += 1
            symptoms = detect_symptoms(
                baseline.select(col),
                current.select(col),
                [col],
                dist_threshold=dist_threshold,
                model=model,
            )
            if symptoms:
                fires += 1
                confidence = max((s.confidence or 0.0) for s in symptoms)
                pairs.append((confidence, 0))
                if len(examples) < 10:
                    fired = ", ".join(s.signature for s in symptoms)
                    examples.append(f"{col}: {fired}")
            else:
                pairs.append((0.0, 0))
    return RealFpReport(
        trials=trials, checks=checks, fires=fires, examples=examples, pairs=pairs
    )


def injected_recall_report(
    df: pl.DataFrame,
    *,
    family: str,
    column: str,
    inject: Callable[[pl.DataFrame], pl.DataFrame],
    n: int,
    trials: int,
    seed: int = 0,
    dist_threshold: float = DEFAULT_DISTRIBUTION_THRESHOLD,
    model: LogisticModel | None = None,
) -> RealRecallReport:
    """Plant a labelled ``family`` change into the current draw and measure recovery.

    Baseline and current are disjoint real draws; ``inject`` is applied only to the
    current draw, so the detector must separate the planted signal from real
    sampling noise. A hit = the expected ``family`` signature fires on ``column``.
    """
    model = _resolve_model(model)
    hits = 0
    pairs: list[Pair] = []
    for baseline, current in two_sample_splits(df, n, trials, seed=seed):
        injected = inject(current)
        symptoms = detect_symptoms(
            baseline.select(column),
            injected.select(column),
            [column],
            dist_threshold=dist_threshold,
            model=model,
        )
        hit = next(
            (s for s in symptoms if s.signature == family and s.column == column), None
        )
        if hit is not None:
            hits += 1
            pairs.append((hit.confidence or 0.0, 1))
    return RealRecallReport(
        family=family, column=column, trials=trials, hits=hits, pairs=pairs
    )
