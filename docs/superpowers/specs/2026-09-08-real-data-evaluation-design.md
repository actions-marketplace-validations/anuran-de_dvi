# Real-data evaluation harness (#15)

**Status:** approved for implementation
**Issue:** [#15](https://github.com/anuran-de/dvi/issues/15)
**Date:** 2026-09-08

## Problem

DVI's headline numbers — "100% recall / 0% false positives", "100% top-1 RCA
accuracy" — are measured against a **synthetic, single-author scenario suite**
(`src/dvi/benchmark/synthetic.py`, `scenarios.py`) plus one real dataset
(`data/diamonds.parquet`, exercised by `real_data.py`). That is credible
*demo-quality* proof, but it is not an independently-reported precision/recall
and calibration result on **real, messy, multi-domain, warehouse-scale** data —
which is what an enterprise evaluator asks for. The synthetic positives use exact
counts and large *n*, so a detector can look perfect there while mis-behaving on
real sampling noise.

## Goal

A real-data evaluation harness, **distinct from the synthetic suite**, that runs
DVI's five detectors over several real datasets from different domains and reports
**precision / recall / false-positive-rate** and **calibration (ECE / MCE /
Brier)** — with the methodology documented and the README claims qualified to
match exactly what has and has not been validated.

Non-goals: naturally-labelled production incidents (they do not exist publicly —
see methodology); changing any detector or the calibration model; a new metric
implementation (reuse `dvi.calibration.reliability`); RCA re-validation (#15 is
detection, not ranking).

## Methodology (and its honesty boundary)

Real, *labelled* "semantic change" incidents are not publicly available. The
harness therefore uses the two experiment shapes already proven in
`real_data.py`, generalised across datasets and all five detectors:

1. **Real-vs-real (specificity / false-positive rate).** Split one real dataset
   into two disjoint samples of the *same* distribution over many seeded trials.
   Nothing changed, so **any** detector that fires is a false positive. This is
   the experiment that catches "false-fires on real noise", which synthetic
   positives cannot.
2. **Injected recall (sensitivity).** Plant a **known, labelled** change into one
   real sample — extended from today's single rename to all five families: value
   substitution, case/format normalisation, category split/merge, numeric
   distribution shift, unit/scale shift — and confirm recovery against real
   sampling noise, with the correct signature on the correct column.

**The honesty boundary, stated in the docs and README:** positives are *injected*
into *real distributions* (real data underneath, ground-truth labels on top), not
observed production incidents. The claim is "validated on real data via
injected-label evaluation + real-vs-real specificity", never "validated on real
production incidents".

## Chosen approach

A **dataset registry**. Each dataset is a declarative entry — an id, a loader
(bundled parquet, or DuckDB over a git-ignored file), the columns that feed
real-vs-real, and the per-family injection recipes for injected-recall. The
engine is dataset-agnostic; adding a domain is a registry entry plus data, no
engine change. The report aggregates per-dataset and pooled.

Rejected alternative: hard-code a second bespoke module per dataset (what
`real_data.py` is for diamonds). It does not scale to four domains and duplicates
the experiment logic five times.

## Components

Each unit has one purpose and is testable in isolation.

### 1. Injectors — pure, real-column change functions

Extend `synthetic.py`'s lone `inject_value_substitution` with one injector per
remaining family, each taking a real `pl.DataFrame`/column and returning a
labelled-positive copy (schema, row-count and null-rate preserved unless the
change itself implies otherwise):

- `inject_case_format(df, column)` — upper/lower/trim an existing category.
- `inject_category_split(df, column, value, into)` — partition rows carrying
  `value` deterministically across the `into` labels (a real split).
- `inject_unit_scale(df, column, factor)` — multiply a numeric column (e.g. ×100,
  dollars→cents).
- `inject_distribution_shift(df, column, pivot, factor)` — non-affine tail
  thickening (`_stretch_above` shape), reused from `scenarios.py`.

All deterministic; no wall clock, no unseeded randomness.

### 2. Experiments — generalised from `real_data.py`

- `real_vs_real_report(df, columns, n, trials, seed, dist_threshold)` — already
  exists; reuse as-is.
- `injected_recall_report` — generalise beyond value-substitution: accept a list
  of `(family, column, injector, expected_signature)` recipes; for each, inject
  into the *current* real draw only, run detection, and count a hit when the
  expected signature fires on the expected column. Baseline/current are disjoint
  real draws, so the detector must separate injected signal from real noise.

Detection routes through the confidence-attaching path (`detect_symptoms(...,
model=<default>)`) so every fired symptom carries a calibrated `confidence`,
which the calibration step needs.

### 3. Calibration on real data — reuse, don't reinvent

Build `(confidence, label)` pairs directly from the labelled real experiments:
each injected-positive trial contributes `(fired_confidence, 1)` and each
real-vs-real check contributes `(confidence_if_fired_else_0, 0)`. Feed the pooled
pairs to the existing `build_reliability_report(pairs)` →
`ReliabilityReport(ece, mce, brier, …)` and `render_reliability`. This measures
whether the **shipped** confidence is calibrated on real data — not a re-fit — so
it answers "are DVI's confidence numbers honest off the synthetic set?".

### 4. Dataset registry

```
RealDataset:
  id: str                       # "diamonds", "adult", "online_retail", "nyc_taxi"
  domain: str                   # human label for the report
  load: Callable[[], pl.DataFrame]   # bundled parquet OR DuckDB-materialised sample
  fp_columns: list[str]         # real-vs-real columns (categorical + numeric)
  recipes: list[InjectionRecipe]     # per-family injected-recall specs
  committed: bool               # True → runs in CI; False → skipped when file absent
```

`build_registry()` returns every dataset whose data is present; absent
git-ignored datasets are skipped (logged), so CI runs the committed subset and a
developer with the taxi file gets the full set.

### 5. Runner + report

`python -m dvi.benchmark.real_eval` loads the registry, runs both experiments per
dataset, computes pooled + per-dataset precision/recall/FP-rate and the
reliability report, and prints a deterministic markdown report. A
`RealEvalReport` dataclass holds the structured result for tests.

### 6. Large-scale / performance dimension (NYC taxi, git-ignored)

- **Prep script** `scripts/prep_nyc_taxi.py`: DuckDB reads the raw 4.3 GB CSV,
  filters to plausible bounds (pickup within the dataset's real month;
  `fare_amount >= 0`; `trip_distance > 0` — the raw file has ~731k negative
  fares, ~776k zero-distance trips, and garbage timestamps spanning 2002–2026),
  and writes a git-ignored `data/local/nyc_taxi.parquet` (tens of millions of
  rows). Deterministic; the raw CSV and the output are both git-ignored.
- The taxi registry entry loads a **DuckDB `REPEATABLE`-seeded reservoir sample**
  for the experiments (deterministic), and the runner records a **throughput
  measurement** (rows/second scanned + profiled at full scale) for the doc.
- Taxi numbers are generated once locally and **committed as text into the
  methodology doc** (they cannot run in CI without the file).

## Data handling & CI split

- **Committed** (parquet at repo root, like diamonds): `diamonds` + `adult` +
  `online_retail` (a subset sized to a few MB). These back the CI-reproducible
  numbers; the harness tests assert on them.
- **Local, git-ignored**: `nyc_taxi` (raw CSV + prepared parquet under
  `data/local/`, added to `.gitignore`). Skipped in CI; its published numbers
  live in the doc.
- New committed datasets are converted to parquet on ingest (no pandas/pyarrow —
  polars/DuckDB only).

## Deliverables

- `src/dvi/benchmark/real_eval/` (registry, experiments, injectors, runner) +
  `python -m dvi.benchmark.real_eval`.
- `scripts/prep_nyc_taxi.py` and a `.gitignore` entry for `data/local/`.
- `docs/validation.md`: methodology (incl. the injected-label honesty boundary),
  per-dataset + pooled precision/recall/FP-rate, the reliability table, and the
  taxi scale/throughput result.
- **README claims qualified**: replace the bare "100% recall / 0% FP" framing with
  a scoped statement ("validated on N real datasets via injected-label evaluation
  and real-vs-real specificity; synthetic-suite numbers labelled as such; not
  independently validated on multi-tenant production incidents") linking to
  `docs/validation.md`.
- `CHANGELOG.md` `## [Unreleased]` entry.

## Determinism & error handling

- Every split, sample, and injection is seeded; DuckDB sampling uses
  `REPEATABLE (<seed>)`. Safe under the alternate-`PYTHONHASHSEED` CI pass.
- A missing git-ignored dataset degrades to "skipped", never a crash; the
  committed subset always runs.
- No new runtime dependency (polars, duckdb, stdlib only).

## Testing (TDD, RED first)

- Unit: each new injector (label-correctness, schema/row-count preserved).
- Unit: `injected_recall_report` recovers each family on a small real frame; a
  sub-threshold decoy stays silent.
- Unit: registry skips an absent git-ignored dataset and includes present ones.
- Integration: full runner on the committed datasets produces a deterministic
  report; pooled recall/FP/calibration fields are present and within sane bounds.
- Docs + `CHANGELOG.md` updated in the same PR.

## Rollout

Single PR: injectors + experiments + registry + runner + taxi prep + docs +
README qualification. Committed datasets make the harness CI-reproducible; the
taxi dimension is developer-run and documented. Maintainer decides merge.
