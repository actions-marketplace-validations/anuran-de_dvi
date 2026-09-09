"""Build the labelled dataset the confidence model is calibrated on.

The dataset mixes three sources so the reliability curve covers the whole
probability range, not just the easy extremes:

* **Positives** — real diamonds samples with a category rename injected across a
  grid of sample size ``n`` and rename ``fraction``. Small ``n`` / small
  ``fraction`` produce *borderline* true changes that populate the middle bins.
* **Negatives** — real-vs-real disjoint splits at deliberately **small ``n``**,
  where sampling noise occasionally slips past the significance guards. These are
  the hard negatives that teach the model to be unsure when the margin is thin.
* **Synthetic** — the M2 labelled scenarios, for multi-signature coverage
  (numeric shift / unit-scale positives the real categorical grid can't provide).

Everything is seeded and derived from profiles, so a build is reproducible and
needs no raw data at inference time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

from ..benchmark._sampling import two_sample_splits
from ..benchmark.real_data import load_diamonds
from ..benchmark.scenarios import build_scenarios
from ..benchmark.synthetic import inject_value_substitution
from ..detection import (
    DEFAULT_DISTRIBUTION_THRESHOLD,
    detect_case_format_normalization,
    detect_category_split_merge,
    detect_numeric_distribution_shift,
    detect_unit_scale_shift,
    detect_value_substitution,
)
from ..profiling import ColumnProfile, profile_column
from .features import FeatureVector, extract_features


@dataclass(frozen=True)
class GridPoint:
    """One injected-positive recipe: rename ``fraction`` of ``from_value`` in a size-n sample."""

    column: str
    from_value: str
    to_value: str
    n: int
    fraction: float


@dataclass(frozen=True)
class LabeledSymptom:
    """A fired symptom's feature vector plus its ground-truth label."""

    features: FeatureVector
    label: int
    signature: str


# Real categorical columns and common values used to synthesise positives.
_POSITIVE_SPECS = [("clarity", "SI1"), ("color", "G"), ("cut", "Ideal")]
_POSITIVE_SIZES = (800, 1500, 3000)
_POSITIVE_FRACTIONS = (0.3, 0.6, 1.0)

# Negatives are drawn small on purpose: this is where noise leaks past the guards.
# The sizes stay small and the trial count high so enough hard negatives survive
# the significance guards to populate the low/middle probability bins.
_NEG_COLUMNS = ["cut", "color", "clarity", "carat", "depth", "table", "price"]
_NEG_SIZES = [50, 80, 120, 200]
_NEG_TRIALS = 25


def default_grid() -> list[GridPoint]:
    """The positive-injection grid: spans small→large ``n`` and rename fraction."""
    points: list[GridPoint] = []
    for column, from_value in _POSITIVE_SPECS:
        for n in _POSITIVE_SIZES:
            for fraction in _POSITIVE_FRACTIONS:
                points.append(
                    GridPoint(column, from_value, f"{from_value}_RECODED", n, fraction)
                )
    return points


def _inject_partial(
    df: pl.DataFrame, column: str, old: str, new: str, fraction: float
) -> pl.DataFrame:
    """Rename the first ``fraction`` of ``old`` rows to ``new`` (deterministic)."""
    if fraction >= 1.0:
        return inject_value_substitution(df, column, old, new)
    is_old = pl.col(column) == old
    total = df.select(is_old.sum()).item() or 0
    threshold = math.ceil(fraction * total)
    rank = is_old.cast(pl.Int64).cum_sum()
    return df.with_columns(
        pl.when(is_old & (rank <= threshold))
        .then(pl.lit(new))
        .otherwise(pl.col(column))
        .alias(column)
    )


def _fired_labeled(
    baseline: ColumnProfile,
    current: ColumnProfile,
    label: int,
    *,
    dist_threshold: float = DEFAULT_DISTRIBUTION_THRESHOLD,
) -> list[LabeledSymptom]:
    """Every signature that fires on this pair, tagged with ``label``."""
    detectors = {
        "value_substitution": detect_value_substitution(baseline, current),
        "case_format_normalization": detect_case_format_normalization(baseline, current),
        "category_split_merge": detect_category_split_merge(baseline, current),
        "numeric_distribution_shift": detect_numeric_distribution_shift(
            baseline, current, threshold=dist_threshold
        ),
        "unit_scale_shift": detect_unit_scale_shift(baseline, current),
    }
    out: list[LabeledSymptom] = []
    for signature, symptom in detectors.items():
        if symptom is not None:
            out.append(
                LabeledSymptom(extract_features(symptom, baseline, current), label, signature)
            )
    return out


def build_calibration_dataset(
    *,
    seed: int = 0,
    df: pl.DataFrame | None = None,
    grid: list[GridPoint] | None = None,
    neg_columns: list[str] | None = None,
    neg_sizes: list[int] | None = None,
    neg_trials: int = _NEG_TRIALS,
    include_synthetic: bool = True,
) -> list[LabeledSymptom]:
    """Assemble the labelled calibration dataset from real + synthetic sources."""
    frame = df if df is not None else load_diamonds()
    grid = grid if grid is not None else default_grid()
    neg_columns = neg_columns if neg_columns is not None else _NEG_COLUMNS
    neg_sizes = neg_sizes if neg_sizes is not None else _NEG_SIZES

    rows: list[LabeledSymptom] = []

    # ---- Positives: injected renames over the grid --------------------------
    for i, point in enumerate(grid):
        baseline, current = two_sample_splits(frame, point.n, 1, seed=seed + 101 + i)[0]
        injected = _inject_partial(
            current, point.column, point.from_value, point.to_value, point.fraction
        )
        pa = profile_column(baseline[point.column])
        pb = profile_column(injected[point.column])
        rows.extend(_fired_labeled(pa, pb, 1))

    # ---- Negatives: small-n real-vs-real (noise that leaks past the guards) --
    for j, size in enumerate(neg_sizes):
        neg_seed = seed + 500 + 37 * j
        for baseline, current in two_sample_splits(frame, size, neg_trials, seed=neg_seed):
            for col in neg_columns:
                pa = profile_column(baseline[col])
                pb = profile_column(current[col])
                rows.extend(_fired_labeled(pa, pb, 0))

    # ---- Synthetic: multi-signature coverage (numeric positives, decoys) ----
    if include_synthetic:
        for scenario in build_scenarios(seed):
            label = 1 if scenario.is_positive else 0
            for col in scenario.columns:
                pa = profile_column(scenario.before[col])
                pb = profile_column(scenario.after[col])
                rows.extend(_fired_labeled(pa, pb, label))

    return rows
