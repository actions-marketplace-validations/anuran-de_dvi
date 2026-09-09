# Changelog

All notable changes to DVI are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed
- Benchmark internals cleanup (#33, #34): `two_sample_splits` now lives in one
  place (`dvi.benchmark._sampling`) instead of being duplicated verbatim across
  `real_data` and `real_eval.experiments`. The legacy diamonds-only helpers were
  renamed to end the name collision with the generalised harness —
  `RealFpReport`/`RealRecallReport` → `DiamondsFpReport`/`DiamondsRecallReport`,
  `real_vs_real_report`/`injected_recall_report` →
  `diamonds_real_vs_real_report`/`diamonds_injected_recall_report`. Behavior is
  unchanged; the `dvi.benchmark.real_eval` API and all reported numbers are
  identical.

### Added
- Auto-derive candidate change events from git commit history in CI, mapping
  changed dbt model files to lineage nodes; `[[changes]]` is now optional and
  is unioned with derived events (#11). Requires `actions/checkout` with
  `fetch-depth: 0`. A run with no declared or derived change now errors.
- Real-data evaluation harness (`python -m dvi.benchmark.real_eval`): specificity
  (real-vs-real) and injected-recall across all five detector families over
  diamonds, adult census, and online-retail, with calibration (ECE/MCE/Brier) on
  real data and a git-ignored NYC-taxi scale dimension. Methodology and results
  in `docs/validation.md`; README claims qualified to match.

## [0.1.0] - 2026-09-04

First public release. Everything below ships in `dvi 0.1.0` on PyPI.

### Incident store — persist history across runs (2026-09-03)

- New `dvi.store` package: an `IncidentStore` interface with a default local
  SQLite backend (stdlib only, no new dependencies).
- Incidents get a deterministic identity (asset + primary signature + change
  event), so re-running the same snapshot upserts onto one row — occurrences and
  last-seen advance, first-seen stays put — instead of duplicating.
- Query API: `history(asset)` (newest first), `get(identity)`, and `prune(before=…)`
  for retention.
- `dvi analyze` records incidents when an optional `[store]` section is present in
  `dvi.toml`; with no section, behavior is unchanged and no database is written.
  Recording never alters the exit code. Run timestamp is the incident's
  detection time (anchored to declared change timestamps, not the wall clock), so
  records are deterministic.
- Docs: [Incident store](docs/incident-store.md). Addresses issue #9.

### Security — warehouse table identifier hardening (2026-09-03)

- Table references in the generated warehouse SQL are now quoted per dotted part
  (`schema.table` → `"schema"."table"`) in `aggregate_query`, `topk_query`, and
  both dialects' `types_query`, trapping any payload inside a single quoted
  identifier instead of interpolating it as raw SQL.
- `WarehouseSource.before_table` / `after_table` are validated at config parse:
  each dot-separated part must be a plain SQL identifier, so a malicious table
  name (e.g. `orders; DROP TABLE users --`) is rejected before any SQL is built.
- Closes the SQL-injection surface tracked in issue #6.

### M6 — Frontend (complete, 2026-09-01)

- Editorial landing page and operator UI (incident dashboard, detail with
  timeline, evidence, and blast-radius graph), built with Next.js + Tailwind
  + Framer Motion, statically exported.
- Fixtures generated from real pipeline runs via `scripts/export_fixtures.py`.
- CI `web` job (typecheck, lint, unit, build, Playwright smoke) and a GitHub
  Pages deploy workflow.

### M5b — CLI + GitHub Action (complete, 2026-08-30)

A `dvi` CLI and a composite GitHub Action drive the pipeline from a single
`dvi.toml`, closing the real-user adoption path: config → source adapter →
render → exit code.

- **cli (config)** — `dvi.toml` (TOML + `tomllib`, validated by pydantic):
  one asset, an optional column subset, a `[source]` (`file` or `warehouse`,
  keyed by `kind`), a `[lineage]` manifest path, one or more `[[changes]]`
  (explicit, RCA-attributed, required ISO-8601 `timestamp`), and a `[gate]`
  (`fail_on` severity, `model` on by default).
- **cli (sources)** — a file adapter (`.parquet`/`.csv`/`.ndjson` via polars)
  and a warehouse adapter (DuckDB, opened read-only, profiling pushed into
  SQL per M5a) both converge on the same `Incident | None` seam.
- **cli (render)** — Markdown + JSON report rendering, written to
  `dvi-report.md` / `dvi-report.json` and echoed to stdout.
- **cli (gate + exit codes)** — a configurable severity gate (`fail_on`)
  drives the process exit code: `0` (no incident, or below gate), `1`
  (gate tripped), `2` (could not run — bad config, missing input, unresolved
  target).
- **cli (entrypoint)** — `dvi analyze --config dvi.toml --output-dir DIR`,
  with `--source-before`/`--source-after` overrides so CI can inject
  PR-specific paths without rewriting the config.
- **action** — a composite GitHub Action that installs DVI, runs `dvi
  analyze`, and posts the report as a **sticky** pull-request comment (keyed
  off a hidden `<!-- dvi-report -->` marker, updated in place on each run) via
  the runner's `gh` CLI, failing the check on the CLI's exit code. No
  third-party action required.
- **docs** — [`docs/cli.md`](docs/cli.md): install, `dvi.toml` reference,
  exit-code table, and the GitHub Action workflow.

223 tests, all green.

### M5a — Warehouse pushdown profiling (complete)

Warehouse pushdown profiling: compute `ColumnProfile` in-warehouse via SQL
(DuckDB executed, Snowflake dialect + SQL-gen tests); `analyze_change_from_profiles`;
cross-engine detection-equivalence.

- **warehouse (dialect)** — `SqlDialect` (abstract) + `DuckDBDialect` +
  `SnowflakeDialect`: each emits per-column profiling SQL (base counts, numeric
  quantiles/stddev/finite-only aggregates, top-K categorical frequencies).
  Snowflake's SQL is unit-tested by string assertion; it is not executed in CI
  because its driver pulls `pyarrow`, which DVI avoids.
- **warehouse (source)** — `SqlProfileSource(execute, table, *, dialect, top_k)`:
  a thin `execute(sql) -> rows` callable is the only connection contract — DVI
  never opens a connection itself — and rows are adapted into the same
  `ColumnProfile` the Polars profiler builds.
- **pipeline** — `analyze_change_from_profiles`, a twin of `analyze_change`, plus
  a shared `detect_symptoms_from_profiles` core so the pushdown path and the
  local Polars path run identical detection logic.
- **tests** — `tests/test_pushdown_equivalence.py`: categorical and numeric
  cases run through DuckDB and Polars and assert decision-identical incidents
  (detection-equivalence, not bit-identical floats).
- **docs** — [`docs/warehouse-pushdown.md`](docs/warehouse-pushdown.md): the
  executor contract, DuckDB and Snowflake usage, and the equivalence guarantee.

180 tests, all green.

### M4 — Blast-radius / business-level impact (complete)

An incident's blast radius now extends past data assets to the business
consumers downstream of them, and their criticality can lift severity.

- **lineage (exposure)** — `Criticality` (an ordered `IntEnum`), `Exposure`,
  `derive_criticality`: dbt *exposures* (dashboards, ML features, applications,
  notebooks, analyses) parsed as typed lineage nodes. Criticality is either an
  explicit `meta.criticality` override or inferred from `type` + dbt
  `maturity` (a customer-facing, high-maturity `application` is CRITICAL).
- **lineage (graph)** — exposures parsed from `manifest.json` alongside models
  as `kind`-tagged nodes with `model → exposure` edges (dangling refs skipped;
  manifests with no exposures are unchanged); `exposures_downstream_of` /
  `data_downstream_of` traverse the same graph as any other lineage query.
- **incidents (impact)** — `BusinessImpact`, `assess_impact`: projects an
  incident's affected assets onto reachable exposures, grouped by type; the
  worst reachable `Criticality` drives `criticality_to_severity`.
  `escalate_severity` raises (never lowers) base severity to the
  consumer-implied severity, gated on materiality (`max_magnitude >=
  MAGNITUDE_MATERIAL`) — an immaterial flicker under a critical dashboard
  stays low. `render_business_impact` renders the grouped, deterministically
  ordered consumer block.
- **incidents (severity)** — a new `critical` severity tier, above `high`,
  reachable only via escalation from a business-critical consumer.
- **incidents (synthesis)** — `synthesize_incident` now attaches
  `Incident.business_impact` (`None` when no exposures are reached) and the
  summary clause names the affected consumers by count and type.
- **benchmark** — a labeled blast-radius suite (with decoys: an immaterial
  change under a critical consumer, a material change under a non-critical
  consumer) scoring exposure precision, exposure recall, and severity
  accuracy — **100%** on all three.
- **demo / benchmark scripts** — `scripts/demo.py` and `scripts/benchmark.py`
  print the business-impact block and the blast-radius benchmark section.

157 tests, all green.

### M3.1 — Hardening pass (review-driven)

A three-perspective code review of the M1–M3 surface (correctness, detection
robustness, calibration honesty) surfaced a ranked defect list. Every finding was
fixed in priority order, each RED-GREEN with a regression test. Where a fix changed
which symptoms fire on the diamonds calibration set, `coefficients.json` was refit;
held-out quality is unchanged throughout (ECE ≈ 0.047, MCE ≈ 0.21, Brier ≈ 0.005).

- **CRITICAL — public API import cycle.** `from dvi.calibration import load_model`
  as the first import in a fresh interpreter raised on a circular import. The
  pipeline now imports from the calibration *submodules*; covered by fresh-process
  import tests.
- **HIGH — one dirty cell fabricated a distribution.** `drop_nulls()` leaves float
  `NaN`/`inf`, which poison polars mean/std/quantiles. Numeric profiling now keeps
  only finite values. Distribution shift also gained a sample-size noise floor
  (`Z·0.38/√n`) so two halves of the same population stop tripping it at small `n`,
  a negligible-spread guard (spread must clear `1e-4·|location|`), and a non-finite
  quantile guard that no longer fails open.
- **HIGH — calibration over-claimed mid-range.** Added **MCE** (worst-bin gap) and a
  mid-range-bin count to the reliability report and frozen metadata; the honesty
  docs now state plainly that intermediate probabilities are not calibration-tested.
  CV folds are **stratified** and single-class training folds are skipped.
- **MED — detector precedence/false positives.** Value substitution defers
  one-to-many / many-to-one shapes to #3 instead of mislabelling the largest pair as
  a rename. Unit/scale shift no longer fires on near-constant columns far from zero
  (spread below `1e-3·|median|` is treated as scale-free; any real large move there
  is still caught by #4).
- **MED — dead feature removed.** The `coverage` feature was a constant 1.0 on the
  calibration set (weight trained to exactly 0); dropped, leaving the **3-feature**
  vector `magnitude`, `significance_margin`, `log10_n`.
- **LOW — correctness/determinism cluster.** Deterministic `top_k` truncation on
  count ties (secondary sort on value); count-weighted pooled proportion
  `(x_a+x_b)/(n_a+n_b)` in the significance floor (was the share midpoint);
  sample-size-aware numeric significance margin (divides by the detector's effective
  bar, not a flat constant); case/format normalization robust to noise-sized tail
  categories (significant-set comparison + MIN_SHARE gate on re-spellings); and a
  characterization test pinning the categorical significance margin as monotonic and
  non-saturating.

127 tests, all green. `coefficients.json` refit and consistent with a fresh rebuild.

### M3 — Calibrated confidence (complete)

Each fired symptom now carries a *measured* probability that it is a real change,
rather than a hand-tuned number. Calibration is proven on held-out data.

- **calibration (model)** — `LogisticModel`: a pure-Python logistic regression
  (no numpy/scipy/sklearn in the stack) fit by deterministic batch gradient descent
  with L2, standardizing features internally and round-tripping to JSON.
- **calibration (features)** — `extract_features`: the uniform feature vector
  `magnitude`, `significance_margin` (effect size in multiples of its noise/threshold
  floor, branching per signature), `log10(min(na, nb))`. (A fourth `coverage` feature
  shipped in M3 was dropped in M3.1 as a dead constant — see above.)
- **calibration (dataset)** — `build_calibration_dataset`: labelled data mixing real
  injected renames over a size×fraction grid (borderline positives), small-`n`
  real-vs-real splits (hard negatives where noise leaks past the guards), and the
  synthetic scenarios (multi-signature coverage). Fully seeded.
- **calibration (reliability)** — `k_fold_predictions`, `reliability_table`,
  `expected_calibration_error`, `brier_score`, `render_reliability`: honesty via
  out-of-fold predictions; a text/markdown reliability table with per-bin counts
  (no plotting library available).
- **calibration (freezing)** — `build_coefficients` / `load_model`: the final model
  is refit on all data and frozen to `coefficients.json` (shipped as package data);
  a regression test re-fits and asserts the coefficients match and held-out ECE/Brier
  stay within bounds.
- **wiring** — `Symptom.confidence` (default `None` keeps M1/M2 behavior),
  `score_symptom` / `attach_confidence`, an optional `model=` on `detect_symptoms`
  and `analyze_change`, and `Incident.confidence` surfacing the primary symptom's
  score. The demo prints a calibrated confidence line.
- **result** — out-of-fold **ECE ≈ 0.045 / Brier ≈ 0.005** on the fired-symptom set
  (refined in M3.1 to 58 symptoms, 34 positive, with MCE ≈ 0.21 now also reported).
  Confidence is conditional on firing, so predictions skew to the extremes; sparse
  middle bins stay visible in the per-bin table.

103 tests, all green. The benchmark prints the calibration section end to end.

### Real-data validation + significance guards

Validating the detectors on a real public dataset (the classic **diamonds** set,
53,940 rows) exposed a robustness gap the synthetic suite hid: running two random
halves of the *same* distribution through the share-based detectors false-fired on
nearly every split at small sample sizes. The synthetic "0% false positives" was an
artifact of exact counts and large n. This change closes the gap.

- **detection (significance)** — `significance.noise_threshold`: a share move now
  counts only if it clears the two-proportion sampling noise `Z·√(p(1-p)(1/na+1/nb))`
  (Z=3.0, three-sigma). Wired into `value_substitution` and `category_split_merge`
  as `max(MIN_SHIFT, noise_threshold(...))`. The same share move is noise at small
  n and signal at large n.
- **detection (unit/scale)** — replaced the joint slope+intercept affine fit, which
  turned quantile jitter on a narrow-spread column (a percentage near 61) into a
  bogus "+4 offset", with two independent single-parameter hypotheses: multiplicative
  (spread ratio, through origin) and additive (median shift, slope fixed at 1). A
  no-op scale reads as factor≈1; a no-op shift reads as offset≈0.
- **benchmark (real data)** — `dvi.benchmark.real_data`: `load_diamonds`,
  `two_sample_splits`, `real_vs_real_report`, `injected_recall_report`,
  `evaluate_real_data`. The diamonds parquet is bundled (~0.5 MB) so CI runs offline.
- **result** — at n=1000 across 30 disjoint splits: **0/210 real-vs-real false
  positives** (down from ~100% of splits before the guards) and **100% recall** of a
  planted category rename. Residual false positives appear only at n≤500 (~1% at
  n=250) and vanish by n=1000 — measured, not hidden.

72 tests, all green. CI runs the real-data validation end to end.

### M2 — Signature taxonomy + benchmark (complete)

The full flagship signature set, plus a labelled benchmark that measures recall
against a false-positive budget.

- **detection** — four new deterministic signatures:
  - `detect_case_format_normalization` (#2): categories re-spelled (casefold +
    whitespace) with the normalized set and masses preserved.
  - `detect_category_split_merge` (#3): one-to-many / many-to-one mass
    redistribution with conserved total mass.
  - `detect_numeric_distribution_shift` (#4): behavioral change in a numeric
    column, measured as normalized quantile movement (tunable threshold).
  - `detect_unit_scale_shift` (#5): rigid affine re-encoding (dollars → cents,
    timezone offset), fitted from robust quantile anchors.
- **pipeline** — detector registry with **precedence**: a more specific
  signature suppresses a more general one on the same column (#5 over #4,
  #2 over #1). Distribution-shift threshold is tunable through `detect_symptoms`.
- **benchmark** — `build_scenarios`: a labelled suite of 5 positives (one per
  signature), 3 normal-variation negatives, and 4 benign decoys (2× volume, a
  new small category, sub-threshold numeric drift). `evaluate` /
  `recall_at_fixed_fp` / `sweep` score recall and false-positive rate and trace
  the operating curve.
- **benchmark (RCA)** — `build_rca_cases` + `evaluate_rca`: labelled root-cause
  cases with concurrent distractor deploys (irrelevant, post-symptom, and weaker
  upstream competitors). Measures top-1 ranking accuracy.
- **demo** — `scripts/benchmark.py`: prints the detection report, operating
  curve, and RCA top-1 accuracy.
- **result** — 100% recall at 0% false positives at the shipped operating point
  (FPs appear below t=0.08, distribution-shift positive missed at t≥0.43); 100%
  top-1 root-cause accuracy under distractors.

60 tests, all green. CI now also runs the benchmark end to end.

### M1 — Walking skeleton (complete)

The thinnest end-to-end path that proves the core hypothesis: a silent semantic
change is detected, attributed to a deploy, and scoped downstream.

- **profiling** — `ColumnProfile` + `profile_column`: distribution-aware column
  profiles (top-K value frequencies, null rate, cardinality).
- **detection** — `detect_value_substitution`: signature #1, a deterministic,
  mass-conserving test for a category being renamed/replaced.
- **lineage** — `load_dbt_manifest` → `LineageGraph` with upstream/downstream
  traversal (backed by networkx, no graph DB).
- **rca** — `rank_root_causes`: corroborates symptoms against change events and
  lineage; only prior, graph-relevant changes become candidates.
- **incidents** — `synthesize_incident`: evidence-backed incident with severity
  and downstream blast radius; returns `None` when uncorroborated (symptom ≠
  incident). No fabricated confidence numbers.
- **pipeline** — `analyze_change`: two snapshots in, an incident out.
- **benchmark** — synthetic orders generator + value-substitution injector.
- **demo** — `scripts/demo.py`: the "silent rename" scenario, end to end.

24 tests, all green.

### Hardening

- **detection** — deterministic lexicographic tie-break for drop/gain matching
  (results no longer depend on set/hash iteration order); high-cardinality guard
  that skips columns whose top_k covers <90% of non-null rows.
- **CI** — GitHub Actions across Python 3.11/3.12: ruff lint, pytest, a
  second test pass under an alternate `PYTHONHASHSEED` (determinism guard), and
  a job that runs the demo end to end. 26 tests.
