"""Validate the detectors on a real public dataset (the classic ``diamonds`` set).

The synthetic benchmark measures recall against decoys, but it cannot answer the
question that decides whether DVI is usable in production: *when nothing changed,
does it stay silent?* Synthetic positives use exact counts and large n, so a
detector can look perfect there while false-firing constantly on real, noisy data.

This module runs two experiments against ``data/diamonds.parquet`` (53,940 rows,
bundled so CI is offline-deterministic):

1. **real-vs-real** — split the data into two disjoint samples of the *same*
   distribution and confirm no detector fires. This is what exposed the original
   share-shift noise cannon and motivated the sample-size-aware significance guards.
2. **injected recall** — plant a known category rename into a real sample and
   confirm it is recovered despite real sampling noise.

Everything is seeded, so results are reproducible run to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from dvi.detection import (
    DEFAULT_DISTRIBUTION_THRESHOLD,
    detect_case_format_normalization,
    detect_category_split_merge,
    detect_numeric_distribution_shift,
    detect_unit_scale_shift,
    detect_value_substitution,
)
from dvi.profiling import profile_column

from ._sampling import two_sample_splits
from .synthetic import inject_value_substitution

# Bundled at the repo root so the benchmark runs offline and deterministically.
DIAMONDS_PATH = Path(__file__).resolve().parents[3] / "data" / "diamonds.parquet"


def load_diamonds() -> pl.DataFrame:
    """Load the bundled diamonds dataset (53,940 rows)."""
    if not DIAMONDS_PATH.exists():
        raise FileNotFoundError(
            f"diamonds dataset not found at {DIAMONDS_PATH}. It is bundled in the repo; "
            "run from a checkout that includes data/diamonds.parquet."
        )
    return pl.read_parquet(DIAMONDS_PATH)


def _firing_detectors(
    baseline: pl.Series, current: pl.Series, *, dist_threshold: float
) -> list[str]:
    """Names of every signature that fires on this column pair (empty = silent)."""
    pa = profile_column(baseline)
    pb = profile_column(current)
    detectors = {
        "value_substitution": detect_value_substitution(pa, pb),
        "case_format_normalization": detect_case_format_normalization(pa, pb),
        "category_split_merge": detect_category_split_merge(pa, pb),
        "numeric_distribution_shift": detect_numeric_distribution_shift(
            pa, pb, threshold=dist_threshold
        ),
        "unit_scale_shift": detect_unit_scale_shift(pa, pb),
    }
    return [name for name, symptom in detectors.items() if symptom is not None]


@dataclass(frozen=True)
class DiamondsFpReport:
    """Real-vs-real false-positive experiment result."""

    trials: int
    checks: int
    fires: int
    examples: list[str] = field(default_factory=list)

    @property
    def false_positive_rate(self) -> float:
        return self.fires / self.checks if self.checks else 0.0


def diamonds_real_vs_real_report(
    df: pl.DataFrame,
    *,
    columns: list[str],
    n: int,
    trials: int,
    seed: int = 0,
    dist_threshold: float = DEFAULT_DISTRIBUTION_THRESHOLD,
) -> DiamondsFpReport:
    """Run every detector over disjoint same-distribution splits; count any firing."""
    checks = 0
    fires = 0
    examples: list[str] = []
    for baseline, current in two_sample_splits(df, n, trials, seed=seed):
        for col in columns:
            checks += 1
            hit = _firing_detectors(baseline[col], current[col], dist_threshold=dist_threshold)
            if hit:
                fires += 1
                if len(examples) < 10:
                    examples.append(f"{col}: {', '.join(hit)}")
    return DiamondsFpReport(trials=trials, checks=checks, fires=fires, examples=examples)


@dataclass(frozen=True)
class DiamondsRecallReport:
    """Injected-change recall experiment result."""

    trials: int
    hits: int

    @property
    def recall(self) -> float:
        return self.hits / self.trials if self.trials else 0.0


def diamonds_injected_recall_report(
    df: pl.DataFrame,
    *,
    column: str,
    from_value: str,
    to_value: str,
    n: int,
    trials: int,
    seed: int = 0,
) -> DiamondsRecallReport:
    """Plant a category rename into a real sample and measure recovery.

    Baseline and current are disjoint real draws; the rename is applied only to
    the current draw, so the detector must separate the injected signal from real
    sampling noise.
    """
    hits = 0
    for baseline, current in two_sample_splits(df, n, trials, seed=seed):
        injected = inject_value_substitution(current, column, from_value, to_value)
        symptom = detect_value_substitution(
            profile_column(baseline[column]), profile_column(injected[column])
        )
        if symptom is not None and to_value in (symptom.from_value, symptom.to_value):
            hits += 1
    return DiamondsRecallReport(trials=trials, hits=hits)


@dataclass(frozen=True)
class RealDataReport:
    """Combined real-data validation: false positives + injected recall."""

    fp: DiamondsFpReport
    recall: DiamondsRecallReport


# Columns exercised in the real-vs-real experiment (categorical + numeric).
_FP_COLUMNS = ["cut", "color", "clarity", "carat", "depth", "table", "price"]


def evaluate_real_data(
    df: pl.DataFrame | None = None, *, n: int = 1000, trials: int = 30
) -> RealDataReport:
    """Run both real-data experiments and return the combined report."""
    frame = df if df is not None else load_diamonds()
    fp = diamonds_real_vs_real_report(frame, columns=_FP_COLUMNS, n=n, trials=trials)
    recall = diamonds_injected_recall_report(
        frame,
        column="clarity",
        from_value="SI1",
        to_value="SI1_RECODED",
        n=max(n, 2000),
        trials=trials,
    )
    return RealDataReport(fp=fp, recall=recall)
