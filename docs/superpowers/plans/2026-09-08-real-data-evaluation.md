# Real-data evaluation harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real-data evaluation harness that runs DVI's five detectors over several real, messy, multi-domain datasets and reports precision/recall/false-positive-rate plus calibration (ECE/MCE/Brier), with README claims qualified to match exactly what is validated.

**Architecture:** A dataset-agnostic engine plus a declarative registry. Each dataset is a `RealDataset` entry (id, loader, real-vs-real columns, per-family injection recipes, committed flag). Two experiments — real-vs-real specificity and injected recall across all five families — route through the confidence-attaching detection path so every fired symptom yields a `(confidence, label)` pair for the existing calibration machinery. A runner aggregates per-dataset and pooled results into a deterministic markdown report. Large-scale NYC-taxi runs locally via DuckDB and its numbers are published as text.

**Tech Stack:** Python 3.11, polars, duckdb (git-ignored large data only), pydantic v2, pytest, ruff (line-length 100). No pandas/pyarrow/sklearn/numpy.

**Spec:** `docs/superpowers/specs/2026-09-08-real-data-evaluation-design.md`

## Global Constraints

- **No new runtime dependency.** Only polars, duckdb, networkx, pydantic, and the stdlib. DuckDB is used only for the git-ignored NYC-taxi path and the one-time dataset-prep scripts.
- **Determinism.** Every split, sample, and injection is seeded. DuckDB sampling uses `USING SAMPLE ... (reservoir, <seed>)` / `REPEATABLE (<seed>)`. Nothing reads the wall clock or unseeded randomness in the decision path. Must pass under an alternate `PYTHONHASHSEED`.
- **TDD, RED first.** Every behavior lands as a failing test, then the minimal code to pass it.
- **ruff line-length 100.** Match the surrounding code's style (module docstrings, `from __future__ import annotations`).
- **Commit authoring (verbatim).** Every commit MUST be authored as Anuran De with NO `Co-Authored-By` trailer and NO "Generated with" line. Use exactly:
  ```bash
  git -c user.name="Anuran De" -c user.email="121761842+anuran-de@users.noreply.github.com" commit -m "<message>" --author="Anuran De <121761842+anuran-de@users.noreply.github.com>"
  ```
- **Branch.** All work lands on `feat/real-data-evaluation` (already checked out). Do not merge; the maintainer decides that.
- **Honesty boundary.** Positives are *injected* into *real distributions* (real data underneath, ground-truth labels on top), never "observed production incidents". Docs and README must say so.
- **Raw data is git-ignored.** `data/raw/adult/adult.csv`, `data/raw/retail/online_retail.parquet`, and `nyc_taxi_data.csv` are git-ignored. Only the small committed parquet *subsets* (`data/adult.parquet`, `data/online_retail.parquet`) enter git, via explicit `!` exceptions.

---

## File Structure

- `src/dvi/benchmark/synthetic.py` — **modify**: add four injectors (case/format, split, unit-scale, distribution-shift) beside the existing `inject_value_substitution`. This is the injection home shared by both suites.
- `src/dvi/benchmark/real_eval/__init__.py` — **create**: package exports.
- `src/dvi/benchmark/real_eval/experiments.py` — **create**: `real_vs_real_report`, `injected_recall_report`, calibration-pair builders — all through the confidence path.
- `src/dvi/benchmark/real_eval/registry.py` — **create**: `RealDataset`, `InjectionRecipe`, `build_registry()`.
- `src/dvi/benchmark/real_eval/runner.py` — **create**: `run_registry()`, `RealEvalReport`, `render_report()`.
- `src/dvi/benchmark/real_eval/__main__.py` — **create**: `python -m dvi.benchmark.real_eval`.
- `scripts/prep_real_datasets.py` — **create**: one-time raw→committed-parquet-subset ingest (adult, online_retail).
- `scripts/prep_nyc_taxi.py` — **create**: DuckDB raw-CSV→`data/local/nyc_taxi.parquet` (git-ignored).
- `data/adult.parquet`, `data/online_retail.parquet` — **create (committed)**: small real subsets for CI.
- `.gitignore` — **modify**: `!` exceptions for the two committed parquets; ignore `data/local/`.
- `docs/validation.md` — **create**: methodology + measured numbers.
- `README.md` / `CHANGELOG.md` — **modify**: qualify claims, changelog entry.
- Tests: `tests/test_real_eval_injectors.py`, `tests/test_real_eval_experiments.py`, `tests/test_real_eval_registry.py`, `tests/test_real_eval_runner.py`.

---

## Task 1: Injection functions for the four remaining families

**Files:**
- Modify: `src/dvi/benchmark/synthetic.py`
- Test: `tests/test_real_eval_injectors.py`

**Interfaces:**
- Consumes: `polars` only.
- Produces (all pure, deterministic, schema/row-count preserving unless the change implies otherwise):
  - `inject_case_format(df: pl.DataFrame, column: str) -> pl.DataFrame` — upper-case every non-null string value in `column`.
  - `inject_category_split(df: pl.DataFrame, column: str, value: str, into: list[str]) -> pl.DataFrame` — deterministically partition the rows whose `column == value` across the `into` labels (round-robin by row position).
  - `inject_unit_scale(df: pl.DataFrame, column: str, factor: float) -> pl.DataFrame` — multiply the numeric `column` by `factor`.
  - `inject_distribution_shift(df: pl.DataFrame, column: str, pivot: float, factor: float) -> pl.DataFrame` — multiply only values `> pivot` by `factor` (non-affine tail thickening).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_real_eval_injectors.py
"""Injection functions plant one labelled change per detector family."""
from __future__ import annotations

import polars as pl

from dvi.benchmark.synthetic import (
    inject_case_format,
    inject_category_split,
    inject_distribution_shift,
    inject_unit_scale,
)


def test_case_format_upper_cases_values_and_preserves_shape():
    df = pl.DataFrame({"c": ["Male", "Female", None, "Male"]})
    out = inject_case_format(df, "c")
    assert out["c"].to_list() == ["MALE", "FEMALE", None, "MALE"]
    assert out.height == df.height


def test_category_split_partitions_only_the_target_value():
    df = pl.DataFrame({"c": ["A", "A", "A", "A", "B"]})
    out = inject_category_split(df, "c", "A", ["A1", "A2"])
    # Round-robin over the four "A" rows in position order; "B" untouched.
    assert out["c"].to_list() == ["A1", "A2", "A1", "A2", "B"]
    assert out.height == df.height


def test_unit_scale_multiplies_numeric_column():
    df = pl.DataFrame({"c": [1.0, 2.5, 3.0]})
    out = inject_unit_scale(df, "c", 100.0)
    assert out["c"].to_list() == [100.0, 250.0, 300.0]


def test_distribution_shift_only_stretches_above_pivot():
    df = pl.DataFrame({"c": [10.0, 60.0, 100.0]})
    out = inject_distribution_shift(df, "c", pivot=55.0, factor=2.0)
    assert out["c"].to_list() == [10.0, 120.0, 200.0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_real_eval_injectors.py -v`
Expected: FAIL with `ImportError` (functions not defined).

- [ ] **Step 3: Implement the four injectors in `synthetic.py`**

Add below `inject_value_substitution` (keep the module's `from __future__ import annotations` and `import polars as pl`):

```python
def inject_case_format(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Return a copy with every non-null string in ``column`` upper-cased.

    The case/format normalisation incident: the vocabulary is preserved, only
    the surface form changes. Nulls and row count are untouched.
    """
    return df.with_columns(pl.col(column).str.to_uppercase().alias(column))


def inject_category_split(
    df: pl.DataFrame, column: str, value: str, into: list[str]
) -> pl.DataFrame:
    """Return a copy where rows carrying ``value`` are split across ``into``.

    The rows whose ``column == value`` are relabelled round-robin (by their
    position among the matching rows) across ``into``; every other row is
    untouched. Deterministic — no randomness. Schema and row count preserved.
    """
    # Position among the matching rows: a running count of prior matches.
    match = pl.col(column) == value
    rank = match.cum_sum() - 1  # 0-based index among matches; ignored for non-matches
    expr = pl.col(column)
    for i, label in enumerate(into):
        expr = (
            pl.when(match & (rank % len(into) == i))
            .then(pl.lit(label))
            .otherwise(expr)
        )
    return df.with_columns(expr.alias(column))


def inject_unit_scale(df: pl.DataFrame, column: str, factor: float) -> pl.DataFrame:
    """Return a copy with the numeric ``column`` multiplied by ``factor``.

    The unit/scale incident: dollars silently re-encoded as cents (``factor=100``).
    """
    return df.with_columns((pl.col(column) * factor).alias(column))


def inject_distribution_shift(
    df: pl.DataFrame, column: str, pivot: float, factor: float
) -> pl.DataFrame:
    """Return a copy with values above ``pivot`` in ``column`` scaled by ``factor``.

    A non-affine tail thickening (the ``_stretch_above`` shape from the synthetic
    scenarios): the body of the distribution is unchanged, only the upper tail
    moves, so it cannot be undone by a simple re-scale.
    """
    return df.with_columns(
        pl.when(pl.col(column) > pivot)
        .then(pl.col(column) * factor)
        .otherwise(pl.col(column))
        .alias(column)
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_real_eval_injectors.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dvi/benchmark/synthetic.py tests/test_real_eval_injectors.py
git -c user.name="Anuran De" -c user.email="121761842+anuran-de@users.noreply.github.com" commit -m "feat(benchmark): add case/split/unit/distribution injectors for real-data eval" --author="Anuran De <121761842+anuran-de@users.noreply.github.com>"
```

---

## Task 2: Generalised experiments through the confidence path

**Files:**
- Create: `src/dvi/benchmark/real_eval/__init__.py`
- Create: `src/dvi/benchmark/real_eval/experiments.py`
- Test: `tests/test_real_eval_experiments.py`

**Interfaces:**
- Consumes: `dvi.pipeline.analyze.detect_symptoms(before, after, columns, *, model)`; `dvi.calibration.loader.load_model()`; `Symptom.signature/column/confidence`; the injectors from Task 1 and `inject_value_substitution`.
- Produces:
  - `@dataclass(frozen=True) RealFpReport(trials, checks, fires, examples, pairs)` where `pairs: list[tuple[float, int]]` are `(confidence_if_fired_else_0.0, 0)` calibration pairs, and `false_positive_rate` property = `fires / checks`.
  - `@dataclass(frozen=True) RealRecallReport(family, column, trials, hits, pairs)` where `pairs` are `(confidence, 1)` for each hit, and `recall` property = `hits / trials`.
  - `two_sample_splits(df, n, trials, *, seed=0) -> list[tuple[pl.DataFrame, pl.DataFrame]]` (same contract as `real_data.two_sample_splits`).
  - `real_vs_real_report(df, *, columns, n, trials, seed=0, dist_threshold=..., model=None) -> RealFpReport`.
  - `injected_recall_report(df, *, family, column, inject, n, trials, seed=0, dist_threshold=..., model=None) -> RealRecallReport` where `inject: Callable[[pl.DataFrame], pl.DataFrame]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_real_eval_experiments.py
"""The generalised real-vs-real and injected-recall experiments."""
from __future__ import annotations

import polars as pl

from dvi.benchmark.real_eval.experiments import (
    injected_recall_report,
    real_vs_real_report,
)
from dvi.benchmark.synthetic import inject_value_substitution


def _stable_frame(n: int = 4000) -> pl.DataFrame:
    # A benign categorical + numeric frame: same distribution across any split.
    cats = ["US", "UK", "DE", "FR"]
    return pl.DataFrame(
        {
            "country": [cats[i % len(cats)] for i in range(n)],
            "amount": [10.0 + (i % 90) for i in range(n)],
        }
    )


def test_real_vs_real_stays_silent_on_same_distribution():
    df = _stable_frame()
    report = real_vs_real_report(
        df, columns=["country", "amount"], n=1000, trials=10
    )
    assert report.checks == 20  # 2 columns x 10 trials
    assert report.fires == 0
    assert report.false_positive_rate == 0.0
    # Silent checks still contribute a (0.0, 0) calibration pair.
    assert len(report.pairs) == 20
    assert all(p == (0.0, 0) for p in report.pairs)


def test_injected_recall_recovers_a_planted_rename():
    df = _stable_frame()
    report = injected_recall_report(
        df,
        family="value_substitution",
        column="country",
        inject=lambda d: inject_value_substitution(d, "country", "UK", "United Kingdom"),
        n=2000,
        trials=10,
    )
    assert report.family == "value_substitution"
    assert report.recall >= 0.9
    # Every hit contributes a (confidence, 1) pair with a real confidence.
    assert len(report.pairs) == report.hits
    assert all(label == 1 and 0.0 <= conf <= 1.0 for conf, label in report.pairs)


def test_injected_recall_ignores_a_subthreshold_decoy():
    df = _stable_frame()
    # A no-op "injection" changes nothing, so nothing should fire.
    report = injected_recall_report(
        df,
        family="value_substitution",
        column="country",
        inject=lambda d: d,
        n=2000,
        trials=10,
    )
    assert report.hits == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_real_eval_experiments.py -v`
Expected: FAIL with `ModuleNotFoundError: dvi.benchmark.real_eval`.

- [ ] **Step 3: Create the package and implement the experiments**

`src/dvi/benchmark/real_eval/__init__.py`:

```python
"""Real-data evaluation harness: specificity + injected recall on real datasets."""
```

`src/dvi/benchmark/real_eval/experiments.py`:

```python
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

Pair = tuple[float, int]


def two_sample_splits(
    df: pl.DataFrame, n: int, trials: int, *, seed: int = 0
) -> list[tuple[pl.DataFrame, pl.DataFrame]]:
    """Return ``trials`` pairs of disjoint size-``n`` samples of the same frame."""
    splits: list[tuple[pl.DataFrame, pl.DataFrame]] = []
    for t in range(trials):
        shuffled = df.sample(fraction=1.0, shuffle=True, seed=seed + t)
        splits.append((shuffled.slice(0, n), shuffled.slice(n, n)))
    return splits


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_real_eval_experiments.py -v`
Expected: PASS (3 tests). If `test_injected_recall_recovers_a_planted_rename` shows recall below 0.9, the injection value must be a category with enough support at `n`; the test uses `UK` in a 4-way even split at `n=2000`, which is ~500 rows — ample. Do not weaken the detector to pass.

- [ ] **Step 5: Commit**

```bash
git add src/dvi/benchmark/real_eval/__init__.py src/dvi/benchmark/real_eval/experiments.py tests/test_real_eval_experiments.py
git -c user.name="Anuran De" -c user.email="121761842+anuran-de@users.noreply.github.com" commit -m "feat(benchmark): generalised real-data experiments through the confidence path" --author="Anuran De <121761842+anuran-de@users.noreply.github.com>"
```

---

## Task 3: Committed real subsets + dataset registry

**Files:**
- Create: `scripts/prep_real_datasets.py`
- Create (committed data): `data/adult.parquet`, `data/online_retail.parquet`
- Modify: `.gitignore`
- Create: `src/dvi/benchmark/real_eval/registry.py`
- Test: `tests/test_real_eval_registry.py`

**Interfaces:**
- Consumes: injectors (Task 1), `inject_value_substitution`, `pl.read_parquet`, `dvi.benchmark.real_data.load_diamonds`.
- Produces:
  - `@dataclass(frozen=True) InjectionRecipe(family: str, column: str, inject: Callable[[pl.DataFrame], pl.DataFrame], n: int)`.
  - `@dataclass(frozen=True) RealDataset(id: str, domain: str, load: Callable[[], pl.DataFrame], fp_columns: list[str], recipes: list[InjectionRecipe], committed: bool)`.
  - `DATA_DIR: Path` (= repo `data/`), `ADULT_PATH`, `ONLINE_RETAIL_PATH`.
  - `dataset_specs() -> list[RealDataset]` — every declared dataset (present or not).
  - `build_registry() -> list[RealDataset]` — only datasets whose data is loadable (absent ones skipped).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_real_eval_registry.py
"""The dataset registry declares datasets and skips absent ones."""
from __future__ import annotations

import polars as pl

from dvi.benchmark.real_eval.registry import (
    InjectionRecipe,
    RealDataset,
    build_registry,
    dataset_specs,
)

_FAMILIES = {
    "value_substitution",
    "case_format_normalization",
    "category_split_merge",
    "numeric_distribution_shift",
    "unit_scale_shift",
}


def test_specs_cover_all_five_families_per_committed_dataset():
    for spec in dataset_specs():
        if not spec.committed:
            continue
        families = {r.family for r in spec.recipes}
        assert families == _FAMILIES, f"{spec.id} missing {_FAMILIES - families}"


def test_build_registry_only_returns_loadable_datasets():
    reg = build_registry()
    ids = {d.id for d in reg}
    # diamonds + adult + online_retail are committed, so always present.
    assert {"diamonds", "adult", "online_retail"} <= ids
    for d in reg:
        frame = d.load()
        assert isinstance(frame, pl.DataFrame)
        assert frame.height > 2 * max(r.n for r in d.recipes)


def test_absent_dataset_is_skipped(monkeypatch):
    import dvi.benchmark.real_eval.registry as reg_mod

    missing = RealDataset(
        id="ghost",
        domain="nowhere",
        load=lambda: (_ for _ in ()).throw(FileNotFoundError("gone")),
        fp_columns=["x"],
        recipes=[
            InjectionRecipe("value_substitution", "x", lambda d: d, n=10)
        ],
        committed=False,
    )
    monkeypatch.setattr(
        reg_mod, "dataset_specs", lambda: [*reg_mod._committed_specs(), missing]
    )
    ids = {d.id for d in reg_mod.build_registry()}
    assert "ghost" not in ids
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_real_eval_registry.py -v`
Expected: FAIL with `ModuleNotFoundError` (registry not created) — and the committed parquets do not exist yet.

- [ ] **Step 3: Write the prep script and generate the committed subsets**

`scripts/prep_real_datasets.py`:

```python
"""One-time ingest of the raw real datasets into small committed parquet subsets.

The raw files (``data/raw/adult/adult.csv``, ``data/raw/retail/online_retail.parquet``)
are git-ignored. This script materialises deterministic, few-MB subsets at
``data/adult.parquet`` and ``data/online_retail.parquet`` that ARE committed, so
the harness runs in CI without the raw data. Run from the repo root:

    python scripts/prep_real_datasets.py
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
RAW_ADULT = ROOT / "data" / "raw" / "adult" / "adult.csv"
RAW_RETAIL = ROOT / "data" / "raw" / "retail" / "online_retail.parquet"
OUT_ADULT = ROOT / "data" / "adult.parquet"
OUT_RETAIL = ROOT / "data" / "online_retail.parquet"

# online_retail is 541k rows; a seeded 60k-row sample keeps the committed file a
# few MB while preserving the messiness (returns, nulls, the country long tail).
RETAIL_SAMPLE_ROWS = 60_000
SEED = 0


def prep_adult() -> None:
    df = pl.read_csv(RAW_ADULT)  # 32,561 rows: small enough to commit whole
    df.write_parquet(OUT_ADULT)
    print(f"wrote {OUT_ADULT} ({df.height} rows)")


def prep_online_retail() -> None:
    df = pl.read_parquet(RAW_RETAIL)
    sample = df.sample(n=RETAIL_SAMPLE_ROWS, shuffle=True, seed=SEED)
    sample.write_parquet(OUT_RETAIL)
    print(f"wrote {OUT_RETAIL} ({sample.height} rows)")


if __name__ == "__main__":
    prep_adult()
    prep_online_retail()
```

Run it:

```bash
python scripts/prep_real_datasets.py
```

Expected: writes `data/adult.parquet` (32561 rows) and `data/online_retail.parquet` (60000 rows). Verify both are a few MB at most: `ls -la data/*.parquet`.

- [ ] **Step 4: Add `.gitignore` exceptions for the committed subsets**

Edit `.gitignore`. After the existing `!/data/README.md` line, add:

```
!/data/adult.parquet
!/data/online_retail.parquet
# Local-only prepared datasets (NYC taxi) — generated, never committed.
/data/local/
```

Verify git now sees the two parquets: `git status --short data/` should list `data/adult.parquet` and `data/online_retail.parquet` as untracked (not ignored), and NOT list anything under `data/raw/`.

- [ ] **Step 5: Implement the registry**

`src/dvi/benchmark/real_eval/registry.py`:

```python
"""Declarative registry of real datasets for the evaluation harness.

Each dataset is one entry: how to load it, which columns feed the real-vs-real
specificity test, and one injection recipe per detector family for the
injected-recall test. The engine never names a dataset; adding a domain is an
entry plus data.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from dvi.benchmark.real_data import load_diamonds
from dvi.benchmark.synthetic import (
    inject_case_format,
    inject_category_split,
    inject_distribution_shift,
    inject_unit_scale,
    inject_value_substitution,
)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
ADULT_PATH = DATA_DIR / "adult.parquet"
ONLINE_RETAIL_PATH = DATA_DIR / "online_retail.parquet"


@dataclass(frozen=True)
class InjectionRecipe:
    """One labelled injected-change spec for a single family + column."""

    family: str
    column: str
    inject: Callable[[pl.DataFrame], pl.DataFrame]
    n: int


@dataclass(frozen=True)
class RealDataset:
    """A registry entry: loader + real-vs-real columns + per-family recipes."""

    id: str
    domain: str
    load: Callable[[], pl.DataFrame]
    fp_columns: list[str]
    recipes: list[InjectionRecipe]
    committed: bool


def _diamonds_spec() -> RealDataset:
    return RealDataset(
        id="diamonds",
        domain="retail pricing",
        load=load_diamonds,
        fp_columns=["cut", "color", "clarity", "carat", "depth", "table", "price"],
        recipes=[
            InjectionRecipe(
                "value_substitution", "clarity",
                lambda d: inject_value_substitution(d, "clarity", "SI1", "SI1_RECODED"),
                n=2000,
            ),
            InjectionRecipe(
                "case_format_normalization", "cut",
                lambda d: inject_case_format(d, "cut"), n=2000,
            ),
            InjectionRecipe(
                "category_split_merge", "clarity",
                lambda d: inject_category_split(d, "clarity", "SI1", ["SI1a", "SI1b"]),
                n=2000,
            ),
            InjectionRecipe(
                "numeric_distribution_shift", "price",
                lambda d: inject_distribution_shift(d, "price", pivot=5000.0, factor=2.0),
                n=2000,
            ),
            InjectionRecipe(
                "unit_scale_shift", "price",
                lambda d: inject_unit_scale(d, "price", 100.0), n=2000,
            ),
        ],
        committed=True,
    )


def _adult_spec() -> RealDataset:
    return RealDataset(
        id="adult",
        domain="census income",
        load=lambda: pl.read_parquet(ADULT_PATH),
        fp_columns=["workclass", "occupation", "income", "age", "hours.per.week", "fnlwgt"],
        recipes=[
            InjectionRecipe(
                "value_substitution", "workclass",
                lambda d: inject_value_substitution(d, "workclass", "?", "Unknown"),
                n=2000,
            ),
            InjectionRecipe(
                "case_format_normalization", "sex",
                lambda d: inject_case_format(d, "sex"), n=2000,
            ),
            InjectionRecipe(
                "category_split_merge", "workclass",
                lambda d: inject_category_split(
                    d, "workclass", "Private", ["Private-A", "Private-B"]
                ),
                n=2000,
            ),
            InjectionRecipe(
                "numeric_distribution_shift", "capital.gain",
                lambda d: inject_distribution_shift(
                    d, "capital.gain", pivot=5000.0, factor=2.0
                ),
                n=2000,
            ),
            InjectionRecipe(
                "unit_scale_shift", "hours.per.week",
                lambda d: inject_unit_scale(d, "hours.per.week", 100.0), n=2000,
            ),
        ],
        committed=True,
    )


def _online_retail_spec() -> RealDataset:
    return RealDataset(
        id="online_retail",
        domain="e-commerce transactions",
        load=lambda: pl.read_parquet(ONLINE_RETAIL_PATH),
        fp_columns=["Country", "Quantity", "UnitPrice"],
        recipes=[
            InjectionRecipe(
                "value_substitution", "Country",
                lambda d: inject_value_substitution(
                    d, "Country", "United Kingdom", "UK"
                ),
                n=2000,
            ),
            InjectionRecipe(
                "case_format_normalization", "Country",
                lambda d: inject_case_format(d, "Country"), n=2000,
            ),
            InjectionRecipe(
                "category_split_merge", "Country",
                lambda d: inject_category_split(
                    d, "Country", "United Kingdom", ["UK-North", "UK-South"]
                ),
                n=2000,
            ),
            InjectionRecipe(
                "numeric_distribution_shift", "UnitPrice",
                lambda d: inject_distribution_shift(
                    d, "UnitPrice", pivot=5.0, factor=3.0
                ),
                n=2000,
            ),
            InjectionRecipe(
                "unit_scale_shift", "UnitPrice",
                lambda d: inject_unit_scale(d, "UnitPrice", 100.0), n=2000,
            ),
        ],
        committed=True,
    )


def _committed_specs() -> list[RealDataset]:
    return [_diamonds_spec(), _adult_spec(), _online_retail_spec()]


def dataset_specs() -> list[RealDataset]:
    """Every declared dataset (committed subset). NYC taxi is appended in Task 5."""
    return _committed_specs()


def build_registry() -> list[RealDataset]:
    """Return only datasets whose data actually loads; skip absent ones."""
    live: list[RealDataset] = []
    for spec in dataset_specs():
        try:
            spec.load()
        except (FileNotFoundError, OSError):
            continue
        live.append(spec)
    return live
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_real_eval_registry.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit (code + committed data together)**

```bash
git add scripts/prep_real_datasets.py src/dvi/benchmark/real_eval/registry.py tests/test_real_eval_registry.py .gitignore data/adult.parquet data/online_retail.parquet
git -c user.name="Anuran De" -c user.email="121761842+anuran-de@users.noreply.github.com" commit -m "feat(benchmark): real-data registry + committed adult/online_retail subsets" --author="Anuran De <121761842+anuran-de@users.noreply.github.com>"
```

---

## Task 4: Runner, report, and `__main__`

**Files:**
- Create: `src/dvi/benchmark/real_eval/runner.py`
- Create: `src/dvi/benchmark/real_eval/__main__.py`
- Modify: `src/dvi/benchmark/real_eval/__init__.py` (export the public surface)
- Test: `tests/test_real_eval_runner.py`

**Interfaces:**
- Consumes: `build_registry`, `RealDataset` (Task 3); `real_vs_real_report`, `injected_recall_report`, `RealFpReport`, `RealRecallReport` (Task 2); `dvi.calibration.reliability.build_reliability_report`, `render_reliability`; `dvi.calibration.loader.load_model`.
- Produces:
  - `@dataclass(frozen=True) DatasetResult(dataset_id, domain, fp: RealFpReport, recalls: list[RealRecallReport])` with `recall` property (mean over families) and `fp_rate` property.
  - `@dataclass(frozen=True) RealEvalReport(datasets: list[DatasetResult], reliability)` with `pooled_recall`, `pooled_fp_rate`, `pooled_hits`, `pooled_positives` properties.
  - `run_registry(*, n=1000, trials=30, seed=0) -> RealEvalReport`.
  - `render_report(report: RealEvalReport) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_real_eval_runner.py
"""The runner aggregates per-dataset + pooled results and a reliability report."""
from __future__ import annotations

from dvi.benchmark.real_eval.runner import RealEvalReport, render_report, run_registry


def test_runner_produces_sane_pooled_metrics_on_committed_data():
    # Small n/trials keep the test fast but exercise the full path.
    report = run_registry(n=800, trials=4, seed=0)
    assert isinstance(report, RealEvalReport)
    ids = {d.dataset_id for d in report.datasets}
    assert {"diamonds", "adult", "online_retail"} <= ids

    # Specificity: real-vs-real must be quiet on real, unchanged data.
    assert report.pooled_fp_rate <= 0.10
    # Sensitivity: planted changes are recovered across families.
    assert report.pooled_recall >= 0.80
    # Calibration pairs exist and carry both classes.
    assert report.reliability.count > 0
    assert report.reliability.positives > 0


def test_render_report_is_deterministic_and_labelled():
    a = render_report(run_registry(n=800, trials=4, seed=0))
    b = render_report(run_registry(n=800, trials=4, seed=0))
    assert a == b
    assert "injected-label" in a.lower()
    assert "Reliability" in a
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_real_eval_runner.py -v`
Expected: FAIL with `ModuleNotFoundError` (runner not created).

- [ ] **Step 3: Implement the runner**

`src/dvi/benchmark/real_eval/runner.py`:

```python
"""Run the registry, aggregate per-dataset + pooled metrics, render markdown.

The same runs feed three numbers at once: false-positive rate (real-vs-real),
recall (injected), and calibration (the pooled (confidence, label) pairs). The
report states the injected-label honesty boundary in prose so a reader cannot
mistake it for observed production incidents.
"""
from __future__ import annotations

from dataclasses import dataclass

from dvi.calibration.loader import load_model
from dvi.calibration.reliability import (
    ReliabilityReport,
    build_reliability_report,
    render_reliability,
)

from .experiments import (
    Pair,
    RealFpReport,
    RealRecallReport,
    injected_recall_report,
    real_vs_real_report,
)
from .registry import RealDataset, build_registry


@dataclass(frozen=True)
class DatasetResult:
    dataset_id: str
    domain: str
    fp: RealFpReport
    recalls: list[RealRecallReport]

    @property
    def fp_rate(self) -> float:
        return self.fp.false_positive_rate

    @property
    def recall(self) -> float:
        hits = sum(r.hits for r in self.recalls)
        trials = sum(r.trials for r in self.recalls)
        return hits / trials if trials else 0.0


@dataclass(frozen=True)
class RealEvalReport:
    datasets: list[DatasetResult]
    reliability: ReliabilityReport

    @property
    def pooled_fp_rate(self) -> float:
        fires = sum(d.fp.fires for d in self.datasets)
        checks = sum(d.fp.checks for d in self.datasets)
        return fires / checks if checks else 0.0

    @property
    def pooled_recall(self) -> float:
        hits = sum(r.hits for d in self.datasets for r in d.recalls)
        trials = sum(r.trials for d in self.datasets for r in d.recalls)
        return hits / trials if trials else 0.0


def _run_dataset(
    spec: RealDataset, *, n: int, trials: int, seed: int, model
) -> tuple[DatasetResult, list[Pair]]:
    frame = spec.load()
    # Cap n so a dataset never asks for more rows than it can split disjointly.
    max_n = frame.height // 2
    fp = real_vs_real_report(
        frame, columns=spec.fp_columns, n=min(n, max_n), trials=trials,
        seed=seed, model=model,
    )
    recalls: list[RealRecallReport] = []
    for recipe in spec.recipes:
        recalls.append(
            injected_recall_report(
                frame, family=recipe.family, column=recipe.column,
                inject=recipe.inject, n=min(recipe.n, max_n), trials=trials,
                seed=seed, model=model,
            )
        )
    pairs: list[Pair] = list(fp.pairs)
    for r in recalls:
        pairs.extend(r.pairs)
    return DatasetResult(spec.id, spec.domain, fp, recalls), pairs


def run_registry(*, n: int = 1000, trials: int = 30, seed: int = 0) -> RealEvalReport:
    """Run both experiments over every present dataset; pool the calibration pairs."""
    model = load_model()
    results: list[DatasetResult] = []
    all_pairs: list[Pair] = []
    for spec in build_registry():
        result, pairs = _run_dataset(
            spec, n=n, trials=trials, seed=seed, model=model
        )
        results.append(result)
        all_pairs.extend(pairs)
    reliability = build_reliability_report(all_pairs)
    return RealEvalReport(datasets=results, reliability=reliability)


def render_report(report: RealEvalReport) -> str:
    """A deterministic markdown report: per-dataset table + pooled + reliability."""
    lines = [
        "# DVI real-data evaluation",
        "",
        "Positives are **injected** into **real distributions** (real data "
        "underneath, ground-truth labels on top) — this is injected-label "
        "evaluation, not observed production incidents. Real-vs-real measures "
        "specificity on unchanged real data.",
        "",
        "| dataset | domain | recall | false-positive rate |",
        "|---------|--------|--------|---------------------|",
    ]
    for d in sorted(report.datasets, key=lambda x: x.dataset_id):
        lines.append(
            f"| {d.dataset_id} | {d.domain} | {d.recall:.3f} | {d.fp_rate:.3f} |"
        )
    lines.append(
        f"| **pooled** | — | {report.pooled_recall:.3f} | "
        f"{report.pooled_fp_rate:.3f} |"
    )
    lines.append("")
    lines.append(render_reliability(report.reliability))
    return "\n".join(lines)
```

`src/dvi/benchmark/real_eval/__main__.py`:

```python
"""``python -m dvi.benchmark.real_eval`` — print the real-data evaluation report."""
from __future__ import annotations

from .runner import render_report, run_registry

if __name__ == "__main__":
    print(render_report(run_registry()))
```

Update `src/dvi/benchmark/real_eval/__init__.py`:

```python
"""Real-data evaluation harness: specificity + injected recall on real datasets."""
from __future__ import annotations

from .runner import RealEvalReport, render_report, run_registry

__all__ = ["RealEvalReport", "render_report", "run_registry"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_real_eval_runner.py -v`
Expected: PASS (2 tests). If `pooled_recall` or `pooled_fp_rate` is outside the asserted band, DO NOT relax the assertion first — investigate: a recipe whose target value is too rare at the sampled `n`, a numeric pivot above the column's range (nothing to stretch), or a `fp_columns` entry that legitimately drifts. Adjust the *recipe/pivot*, not the detector. Record the real observed numbers for Task 6.

- [ ] **Step 5: Full determinism check + commit**

Run: `python -m pytest tests/ -q` then `PYTHONHASHSEED=1 python -m pytest tests/test_real_eval_runner.py -q`
Expected: all green under both.

```bash
git add src/dvi/benchmark/real_eval/runner.py src/dvi/benchmark/real_eval/__main__.py src/dvi/benchmark/real_eval/__init__.py tests/test_real_eval_runner.py
git -c user.name="Anuran De" -c user.email="121761842+anuran-de@users.noreply.github.com" commit -m "feat(benchmark): real-data eval runner, report, and python -m entrypoint" --author="Anuran De <121761842+anuran-de@users.noreply.github.com>"
```

---

## Task 5: NYC-taxi scale dimension (git-ignored, developer-run)

**Files:**
- Create: `scripts/prep_nyc_taxi.py`
- Modify: `src/dvi/benchmark/real_eval/registry.py` (append a taxi spec that loads `data/local/nyc_taxi.parquet` only when present)
- Test: extend `tests/test_real_eval_registry.py` with a skip-when-absent assertion for taxi.

**Interfaces:**
- Consumes: duckdb (script only), `pl.read_parquet`.
- Produces: `NYC_TAXI_PATH: Path` (= `data/local/nyc_taxi.parquet`); `dataset_specs()` now appends `_nyc_taxi_spec()` (committed=False), which `build_registry()` already skips when the file is absent.

- [ ] **Step 1: Write the failing test (taxi absent → skipped, spec still declared)**

Add to `tests/test_real_eval_registry.py`:

```python
def test_nyc_taxi_spec_declared_but_skipped_when_file_absent():
    from dvi.benchmark.real_eval.registry import NYC_TAXI_PATH, build_registry, dataset_specs

    assert "nyc_taxi" in {s.id for s in dataset_specs()}
    if not NYC_TAXI_PATH.exists():
        assert "nyc_taxi" not in {d.id for d in build_registry()}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_real_eval_registry.py::test_nyc_taxi_spec_declared_but_skipped_when_file_absent -v`
Expected: FAIL (`NYC_TAXI_PATH`/`nyc_taxi` not defined).

- [ ] **Step 3: Write the prep script**

`scripts/prep_nyc_taxi.py`:

```python
"""Prepare the git-ignored NYC-taxi parquet for the scale dimension of the harness.

DuckDB streams the raw 4.3 GB CSV (``nyc_taxi_data.csv`` at the repo root),
filters to plausible bounds (the raw file has ~731k negative fares, ~776k
zero-distance trips, and timestamps spanning 2002–2026), and writes a
deterministic parquet to ``data/local/nyc_taxi.parquet`` — both raw and output
are git-ignored. Run from the repo root:

    python scripts/prep_nyc_taxi.py
"""
from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "nyc_taxi_data.csv"
OUT = ROOT / "data" / "local" / "nyc_taxi.parquet"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        """
        COPY (
            SELECT
                VendorID, tpep_pickup_datetime, tpep_dropoff_datetime,
                passenger_count, trip_distance, PULocationID, DOLocationID,
                payment_type, fare_amount, tip_amount, total_amount
            FROM read_csv_auto(?)
            WHERE fare_amount >= 0
              AND trip_distance > 0
              AND tpep_pickup_datetime >= TIMESTAMP '2010-01-01'
              AND tpep_pickup_datetime <  TIMESTAMP '2025-01-01'
        ) TO ? (FORMAT parquet)
        """,
        [str(RAW), str(OUT)],
    )
    rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(OUT)]).fetchone()[0]
    print(f"wrote {OUT} ({rows} rows)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Append the taxi spec to the registry**

In `src/dvi/benchmark/real_eval/registry.py`, add near the paths:

```python
NYC_TAXI_PATH = DATA_DIR / "local" / "nyc_taxi.parquet"
```

Add the spec factory (numeric-heavy: taxi is mostly numeric, with `payment_type` as the categorical):

```python
def _nyc_taxi_spec() -> RealDataset:
    return RealDataset(
        id="nyc_taxi",
        domain="urban mobility (scale)",
        load=lambda: pl.read_parquet(NYC_TAXI_PATH),
        fp_columns=["payment_type", "trip_distance", "fare_amount", "total_amount"],
        recipes=[
            InjectionRecipe(
                "value_substitution", "payment_type",
                lambda d: inject_value_substitution(d, "payment_type", "1", "credit"),
                n=5000,
            ),
            InjectionRecipe(
                "case_format_normalization", "payment_type",
                lambda d: inject_case_format(d, "payment_type"), n=5000,
            ),
            InjectionRecipe(
                "category_split_merge", "payment_type",
                lambda d: inject_category_split(d, "payment_type", "1", ["1a", "1b"]),
                n=5000,
            ),
            InjectionRecipe(
                "numeric_distribution_shift", "fare_amount",
                lambda d: inject_distribution_shift(d, "fare_amount", pivot=20.0, factor=2.0),
                n=5000,
            ),
            InjectionRecipe(
                "unit_scale_shift", "total_amount",
                lambda d: inject_unit_scale(d, "total_amount", 100.0), n=5000,
            ),
        ],
        committed=False,
    )
```

Change `dataset_specs()` to append it:

```python
def dataset_specs() -> list[RealDataset]:
    """Every declared dataset. NYC taxi is git-ignored and skipped when absent."""
    return [*_committed_specs(), _nyc_taxi_spec()]
```

Note: `payment_type` is read from CSV as an integer; after `pl.read_parquet` it is an integer column. `inject_case_format`/`inject_value_substitution` require strings. So in `_nyc_taxi_spec`, cast at load time — change the loader to:

```python
load=lambda: pl.read_parquet(NYC_TAXI_PATH).with_columns(
    pl.col("payment_type").cast(pl.Utf8)
),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_real_eval_registry.py -v`
Expected: PASS. The taxi test passes via the skip branch (no local file in CI). `test_specs_cover_all_five_families_per_committed_dataset` still holds (taxi is `committed=False`, so it is skipped by that test's guard).

- [ ] **Step 6: Generate taxi numbers locally (developer machine only) and capture them**

This step runs ONLY where `nyc_taxi_data.csv` is present (not CI). Run:

```bash
python scripts/prep_nyc_taxi.py
python -m dvi.benchmark.real_eval > /tmp/real_eval_report.md
```

Copy the `nyc_taxi` row and the pooled/reliability lines from the printed report — they go verbatim into `docs/validation.md` in Task 6. Also time a full-scale profiling pass for a throughput figure:

```bash
python -c "import time, duckdb; from pathlib import Path; p='data/local/nyc_taxi.parquet'; t=time.perf_counter(); n=duckdb.connect().execute(f\"select count(*), avg(fare_amount) from read_parquet('{p}')\").fetchone(); dt=time.perf_counter()-t; print(n, f'{n[0]/dt:,.0f} rows/s')"
```

Record rows and rows/second for the doc.

- [ ] **Step 7: Commit (script + registry; no data file)**

```bash
git add scripts/prep_nyc_taxi.py src/dvi/benchmark/real_eval/registry.py tests/test_real_eval_registry.py
git -c user.name="Anuran De" -c user.email="121761842+anuran-de@users.noreply.github.com" commit -m "feat(benchmark): NYC-taxi scale dimension (git-ignored, developer-run)" --author="Anuran De <121761842+anuran-de@users.noreply.github.com>"
```

---

## Task 6: Documentation — `docs/validation.md`, README qualification, CHANGELOG

**Files:**
- Create: `docs/validation.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:** none (docs only). Use the real numbers observed in Task 4 Step 4 and Task 5 Step 6.

- [ ] **Step 1: Generate the committed-dataset numbers**

Run: `python -m dvi.benchmark.real_eval`
Copy the per-dataset table (diamonds/adult/online_retail), pooled row, and the reliability block from the output.

- [ ] **Step 2: Write `docs/validation.md`**

Structure (fill the tables with the copied real numbers — no invented figures):

```markdown
# Validation on real data

DVI's detectors are validated on real, messy, multi-domain public datasets, not
only the synthetic scenario suite. This page reports how they behave on real
sampling noise (specificity) and on labelled changes planted into real
distributions (sensitivity), plus whether the shipped confidence numbers are
calibrated off the synthetic set.

## Methodology and its honesty boundary

Real, *labelled* semantic-change incidents are not publicly available. The
harness therefore uses two experiment shapes over real data:

- **Real-vs-real (specificity / false-positive rate).** One real dataset is split
  into two disjoint samples of the same distribution over many seeded trials.
  Nothing changed, so any detector that fires is a false positive.
- **Injected recall (sensitivity).** A known, labelled change — one per detector
  family — is planted into one real sample and must be recovered against real
  sampling noise, with the right signature on the right column.

**Honesty boundary:** positives are *injected* into *real distributions* (real
data underneath, ground-truth labels on top), never observed production
incidents. The claim is "validated on real data via injected-label evaluation
and real-vs-real specificity", not "validated on real production incidents".

## Datasets

| dataset | domain | rows (committed subset) | why it is messy |
|---------|--------|-------------------------|-----------------|
| diamonds | retail pricing | 53,940 | real price/measurement spread |
| adult | census income | 32,561 | `?` missing token, mixed categoricals |
| online_retail | e-commerce transactions | 60,000 (of 541,909) | returns (negative qty), 25% null CustomerID, price outliers |
| nyc_taxi | urban mobility (scale) | tens of millions (local only) | negative fares, zero-distance trips, garbage timestamps |

## Results (committed datasets, CI-reproducible)

<!-- paste the per-dataset + pooled table from `python -m dvi.benchmark.real_eval` -->

## Calibration on real data

<!-- paste the reliability table (ECE / MCE / Brier) from the same output -->

The confidence model is **not re-fit** here — these pairs test whether the
shipped confidence (fit on the synthetic set) stays honest on real data.

## Scale (NYC taxi, developer-run)

<!-- paste the nyc_taxi row + rows and rows/second from scripts/prep_nyc_taxi.py -->

Reproduce: `python scripts/prep_nyc_taxi.py` then `python -m dvi.benchmark.real_eval`.

## Reproduce the committed-dataset numbers

    python -m dvi.benchmark.real_eval
```

- [ ] **Step 3: Qualify the README claims**

Find the headline metrics in `README.md` (the "100% recall / 0% false positives" / "100% top-1 RCA" framing). Replace the bare framing with a scoped statement and a link. Example replacement text:

```markdown
On the synthetic scenario suite the detectors reach 100% recall at 0% false
positives; on real, messy public datasets (diamonds, adult census, online
retail) they are validated by **injected-label evaluation** (known changes
planted into real distributions) and **real-vs-real specificity** (no firing on
unchanged real data). Synthetic-suite numbers are labelled as such and DVI is
**not** independently validated on multi-tenant production incidents. See
[docs/validation.md](docs/validation.md).
```

Keep the exact synthetic numbers only where they are labelled "synthetic suite".

- [ ] **Step 4: Add the CHANGELOG entry**

Under `## [Unreleased]` in `CHANGELOG.md`, add:

```markdown
### Added
- Real-data evaluation harness (`python -m dvi.benchmark.real_eval`): specificity
  (real-vs-real) and injected-recall across all five detector families over
  diamonds, adult census, and online-retail, with calibration (ECE/MCE/Brier) on
  real data and a git-ignored NYC-taxi scale dimension. Methodology and results
  in `docs/validation.md`; README claims qualified to match.
```

- [ ] **Step 5: Verify links and commit**

Run: `python -m pytest tests/ -q` (nothing should regress) and confirm `docs/validation.md` and the README link resolve.

```bash
git add docs/validation.md README.md CHANGELOG.md
git -c user.name="Anuran De" -c user.email="121761842+anuran-de@users.noreply.github.com" commit -m "docs(benchmark): validation.md, qualified README claims, changelog for real-data eval" --author="Anuran De <121761842+anuran-de@users.noreply.github.com>"
```

---

## Self-Review (completed during authoring)

**Spec coverage:**
- Methodology (real-vs-real + injected recall, honesty boundary) → Tasks 2, 4, 6. ✓
- Injectors for all five families → Task 1 (+ existing `inject_value_substitution`). ✓
- Experiments generalised through the confidence path → Task 2. ✓
- Calibration reuse (`build_reliability_report` on real pairs) → Tasks 2 (pairs) + 4 (report). ✓
- Dataset registry (present-only, committed flag) → Task 3. ✓
- Runner + `python -m dvi.benchmark.real_eval` → Task 4. ✓
- NYC-taxi prep + git-ignored scale dimension → Task 5. ✓
- Data-handling / CI split (committed parquet subsets, `.gitignore`) → Task 3. ✓
- Deliverables (`docs/validation.md`, README qualification, CHANGELOG) → Task 6. ✓
- Determinism + skip-not-crash on absent data → Tasks 2/3/4 (seeded), Task 3/5 (skip). ✓

**Placeholder scan:** No TBD/TODO; docs steps that paste measured numbers are gated on real runs (Task 4 Step 4, Task 5 Step 6), not invented figures — deliberate, since the plan must not fabricate metrics.

**Type consistency:** `RealFpReport`/`RealRecallReport` defined in Task 2 and consumed by name in Task 4; `RealDataset`/`InjectionRecipe` defined in Task 3 and consumed in Tasks 4/5; `detect_symptoms(before, after, columns, *, dist_threshold, model)`, `load_model()`, `build_reliability_report(pairs)`, and `render_reliability(report)` match the real signatures read from the codebase.

**Known executor watch-points (not placeholders — real risks to verify):**
- Numeric pivots (`price` 5000, `capital.gain` 5000, `UnitPrice` 5.0, `fare_amount` 20.0) are chosen inside each column's observed range; if a sampled draw has too little tail above the pivot, recall for that one recipe drops — adjust the pivot down, never the detector.
- `capital.gain` is 0 for most adult rows; the distribution-shift recipe stretches the non-zero tail. If recall is weak, lower the pivot toward the non-zero mass (verify with a quick quantile check), not the threshold.
```
