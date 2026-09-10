<h1 align="center">DVI — Data Versioning Intelligence</h1>

<p align="center">
  <em>Catch the data incidents that pass every green check.</em>
</p>

<p align="center">
  <a href="https://github.com/anuran-de/dvi/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/anuran-de/dvi/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://dvintelligence.vercel.app"><img alt="live demo" src="https://img.shields.io/badge/live%20demo-dvintelligence.vercel.app-black"></a>
  <img alt="status" src="https://img.shields.io/badge/status-alpha-orange">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-green"></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#the-signatures">Signatures</a> ·
  <a href="#documentation">Docs</a> ·
  <a href="#contributing">Contributing</a> ·
  <a href="https://dvintelligence.vercel.app">Live demo</a>
</p>

---

DVI is a self-hostable intelligence layer that detects **semantic** data
incidents — the ones where the pipeline runs green but a business number goes
silently wrong. It sits **on top of** your existing stack (dbt, your warehouse,
Git); it does not replace any of it. Detection and root-cause ranking are
**deterministic and explainable** — no LLM sits in the decision path.

## The problem DVI exists to solve

Data pipelines fail differently from software. A pipeline can **execute
successfully, keep its schema, meet its freshness SLA, and pass every
null/volume check** — and still make the business number silently wrong.

```sql
-- The whole pipeline stays green. The revenue dashboard is now wrong.
- WHERE status = 'completed'
+ WHERE status = 'COMPLETE'
```

```text
Before                         After
country = "UK"                 country = "United Kingdom"
```

Schema unchanged. Row count unchanged. Freshness normal. Yet downstream logic
that expects `"UK"` now under-counts a region, and executive revenue drifts by
double digits.

Structural/volumetric observability tools do not catch this class of failure,
because **nothing structural changed**. DVI is built specifically for it.

## Quick start

Requires **Python 3.11+**.

```bash
pip install dvi          # provides the `dvi` command
```

Or from source, for hacking on DVI (see [Contributing](#contributing)):

```bash
git clone https://github.com/anuran-de/dvi.git
cd dvi
python -m venv .venv
source .venv/bin/activate        # Windows (Git Bash): source .venv/Scripts/activate
pip install -e ".[dev]"
```

**See it catch a real incident** (a deploy silently renames `"UK"` → `"United Kingdom"`):

```bash
python scripts/demo.py
```

```text
  Structural checks: schema OK | row_count OK | freshness OK | nulls OK

  DATA INCIDENT
  Severity    : HIGH
  Confidence  : 95% (calibrated, out-of-fold ECE 0.05)

  Suspected data incident from change 'deploy #482 (country normalization)'.
  Value 'UK' (20.0% of the distribution) appears replaced by 'United Kingdom'
  (20.0%) on model.shop.fact_orders; 3 downstream asset(s) affected.

  Evidence:
    * Change 'deploy #482 ...' was deployed 2 min before the first symptom.
    * 'deploy #482 ...' directly changed model.shop.fact_orders, where DVI
      observed: Value 'UK' (20.0%) appears replaced by 'United Kingdom' (20.0%).
    * No corresponding change was observed upstream of the targeted asset(s).
```

### Use it on your own data

DVI runs from a single `dvi.toml` describing one asset, its before/after
snapshots, its dbt lineage, and the changes to attribute against:

```toml
asset = "model.shop.fct_orders"
columns = ["revenue", "country"]          # optional; omit = all shared columns

[source]                                  # "file" or "warehouse"
kind = "file"
before = "artifacts/fct_orders.main.parquet"
after  = "artifacts/fct_orders.pr.parquet"

[lineage]
manifest = "target/manifest.json"         # dbt manifest → models + exposures

[[changes]]                               # optional; RCA attributes to these too
id = "pr-1234"
label = "Refactor revenue rollup"
targets = ["model.shop.stg_orders"]
timestamp = 2026-08-30T12:00:00Z

[gate]
fail_on = "high"                          # low | medium | high | critical
```

```bash
dvi analyze --config dvi.toml --output-dir .dvi
```

In CI, DVI auto-derives candidate change events from the commits in the PR
range and maps changed dbt model files to the assets they touch, so
`[[changes]]` is optional. Declare `[[changes]]` to add events git can't see
(e.g. an upstream vendor load); explicit and derived events are unioned. If
neither a declared nor a derived change exists, the run errors (exit 2).

It writes `.dvi/dvi-report.md` + `.dvi/dvi-report.json` and sets the exit code:
`0` (clean or below gate), `1` (gate tripped), `2` (could not run). Full
reference: [docs/cli.md](docs/cli.md).

### In CI (GitHub Action)

DVI ships a composite Action that runs on a pull request and posts the report as
a **sticky** comment, failing the check when the gate trips:

```yaml
name: DVI
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  dvi:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # DVI derives change events from commit history
      - uses: anuran-de/dvi@main
        with:
          config: dvi.toml
```

A ready-to-copy workflow ships at `.github/workflows/dvi-example.yml`.

## How it works

DVI turns two snapshots of one asset into an evidence-backed incident (or a
clean bill of health):

1. **Profiles** each column over time — including *value distributions*, not
   just counts.
2. **Detects change signatures** — deterministic tests that recognise the
   statistical fingerprint of a semantic change (e.g. a dominant category's mass
   collapsing while a new value absorbs it) without needing to "understand"
   meaning.
3. **Corroborates** a detected change against deployments/commits and downstream
   lineage — an isolated blip stays a *symptom*; only a change that correlates
   with a deploy and propagates downstream becomes an *incident*.
4. **Ranks likely root causes with evidence** — every claim is backed by
   observable facts, never a fabricated confidence number.
5. **Names the business-level impact** — dbt exposures downstream (dashboards,
   ML features, applications) are named by type, and a business-critical
   consumer can escalate severity into a `critical` tier — only for a *material*
   change.

Two capabilities make it production-ready at scale:

- **Warehouse pushdown profiling** — step 1 can run **inside the warehouse**: a
  `SqlDialect` (DuckDB, Snowflake) computes the profile in SQL and only the
  compact result comes back, so profiling a billion-row table moves a handful of
  aggregates, not the table. See [docs/warehouse-pushdown.md](docs/warehouse-pushdown.md).
- **Incident history** — detection is stateless by default, but an optional
  local store gives incidents a stable identity so recurrences dedupe and an
  asset's history is queryable over time. Opt in with `[store]` in `dvi.toml`.
  See [docs/incident-store.md](docs/incident-store.md).

### Design principles

- **Integrate, don't replace.** Reuse dbt's lineage, your warehouse, DuckDB.
  Build only the intelligence layer from scratch.
- **Deterministic first.** Detection and ranking are deterministic and
  explainable. An LLM, if used at all, only *narrates* evidence — it never
  decides whether something changed.
- **Evidence before explanation.** Every root-cause claim carries the observable
  facts that support it.
- **Honest confidence.** No hand-tuned "92%". Confidence is either omitted (rank
  + evidence) or *calibrated and measured on held-out data* — a logistic model
  whose out-of-fold reliability is reported (ECE ≈ 0.05), not asserted.
- **Symptom ≠ incident.** Corroboration (time × deployment × downstream
  propagation) is required before anything pages a human.

## The signatures

Each is a deterministic test over two column profiles. More specific signatures
suppress more general ones on the same column (a rigid `×100` re-encoding reports
as *unit/scale shift*, not a generic distribution shift).

| # | Signature | Catches |
|---|-----------|---------|
| 1 | Value substitution | A category renamed/replaced (`"UK"` → `"United Kingdom"`) |
| 2 | Case/format normalization | Same categories re-spelled (`"active"` → `"ACTIVE"`, stray whitespace) |
| 3 | Category split/merge | One category fans into many, or many collapse into one |
| 4 | Numeric distribution shift | A behavioral change in shape/location of a numeric column |
| 5 | Unit/scale shift | A rigid re-encoding (dollars → cents, timezone offset) |

Commodity signatures (null-explosion, cardinality, volume, duplicate-rate,
schema/type) are slotted in where cheap.

## Why you can trust it

On the **synthetic scenario suite** the detectors reach 100% recall at 0% false
positives; on **real, messy public datasets** (diamonds, adult census, online
retail) they are validated by **injected-label evaluation** (known changes
planted into real distributions) and **real-vs-real specificity** (near-silence
on unchanged real data), reaching high recall at near-zero false positives.
Synthetic-suite numbers below are labelled as such, and DVI is **not**
independently validated on multi-tenant production incidents. Full methodology
and results: [docs/validation.md](docs/validation.md).

DVI is measured against decoys, not just positives — the hard test for a change
detector is staying **silent when nothing changed**.

<details>
<summary><strong>Benchmark (synthetic suite) — 100% recall at 0% false positives</strong></summary>

```bash
python scripts/benchmark.py
```

The credibility comes from the **decoys**: legitimate changes that superficially
look like incidents and must stay silent (a 2× jump in volume, a new market at
1.5% share, sub-threshold numeric drift). The runner sweeps the one continuous
knob (the distribution-shift threshold) to trace the operating curve:

```text
  At the shipped default threshold (0.10):
    recall              : 100%
    false-positive rate : 0%

  Operating curve (distribution-shift threshold sweep):
     threshold   recall  fp_rate   false positives
          0.01     100%      43%   3 numeric decoys/negatives fire
          0.08     100%       0%   -
          0.43      80%       0%   distribution-shift positive missed
```

The safe band `[0.08, 0.43)` gives full recall at zero false positives — a
trade-off that is *measured*, not asserted. The same runner scores root-cause
ranking under concurrent distractor deploys: **100% top-1 accuracy**.
</details>

<details>
<summary><strong>Validated on real data (diamonds, adult census, online retail)</strong></summary>

A synthetic benchmark can flatter its own detector. So DVI is validated against
three real public datasets via `python -m dvi.benchmark.real_eval`: each is
split into **disjoint samples of the same distribution** (real-vs-real, where
any fired symptom is by construction a false positive), plus one known change
per detector family injected into a real sample and checked for recall.

```text
  Results (committed datasets, CI-reproducible)
  pooled recall              : 99.8%
  pooled false-positive rate : 1.9%
```

Numbers are not reported away when they aren't perfect, and every gap is chased
to its real cause. The diamonds `cut` case-format recipe once recovered only
43%: the case detector abstained whenever a low-share category jittered across
the 3% relevance floor between two samples — a pure sampling artifact on `cut`'s
2.98% "Fair" category, mistaken for a real category change. Fixing that (abstain
only on a category truly absent on one side, never one merely jittering) lifted
it to 100% with **byte-identical specificity**, not by tuning a threshold. An
earlier gap on diamonds — a share moving 3 points is a real event at 250k rows
and pure sampling noise at 250 — was closed the same way, via a
**sample-size-aware significance guard**. Full per-dataset numbers, calibration
on real data, and a git-ignored NYC-taxi scale run are in
[docs/validation.md](docs/validation.md).
</details>

<details>
<summary><strong>Calibrated confidence, proven out-of-fold</strong></summary>

When the model says 0.7, about 70% of such symptoms are real — and that's proven
on held-out data, not hand-tuned. The model is a small pure-Python logistic
regression (no numpy/sklearn) over three features: `magnitude`,
`significance_margin`, and `log10` of the sample size.

```text
  Calibrated confidence (per-symptom, k-fold cross-validated)
  Dataset: 58 fired symptoms, 34 real (59% positive)
  Out-of-fold ECE: 0.0466   MCE: 0.2165   Brier: 0.0051
```

Every row is scored by a model that never trained on it; the shipped model is
refit on all data and frozen to JSON, so inference needs no training data.
</details>

## Documentation

| Doc | What's in it |
|-----|--------------|
| [docs/architecture.md](docs/architecture.md) | System design and the module map |
| [docs/cli.md](docs/cli.md) | `dvi.toml` reference, `dvi analyze`, exit codes, the GitHub Action |
| [docs/warehouse-pushdown.md](docs/warehouse-pushdown.md) | In-warehouse profiling, the executor contract, DuckDB/Snowflake |
| [docs/incident-store.md](docs/incident-store.md) | Persisting incident history across runs |
| [docs/frontend.md](docs/frontend.md) | The web UI (landing + operator dashboard) and how to deploy it |
| [docs/validation.md](docs/validation.md) | Real-data evaluation methodology, results, and calibration |
| [CHANGELOG.md](CHANGELOG.md) | Full per-milestone history (M1 → M6) |

**Live demo:** the operator UI is deployed at
**[dvintelligence.vercel.app](https://dvintelligence.vercel.app)** — an
editorial landing page plus an incident dashboard, detail timeline, and
blast-radius graph, all rendered from real pipeline output.

## Roadmap

DVI is built as a **walking skeleton** — the riskiest, most novel part (does
semantic detection + causal ranking actually work?) is proven first; UI and
connectors come last. Every milestone below is complete and green in CI; see the
[CHANGELOG](CHANGELOG.md) for the detail.

| Milestone | Adds | Proves |
|-----------|------|--------|
| **M1** ✅ | Value-substitution signature end-to-end on synthetic data | The core hypothesis is alive |
| **M2** ✅ | Signatures 2–5 + negatives/decoys benchmark + real-data validation | Full recall on the synthetic suite; **0 false positives on real same-distribution data** on an initial single-dataset check, later broadened into a 3-dataset injected-label + real-vs-real harness (pooled recall 99.8%, fp 1.9% — see [docs/validation.md](docs/validation.md)) |
| **M3** ✅ | Calibrated logistic confidence + out-of-fold reliability table | Honest, *measured* confidence (ECE ≈ 0.05) |
| **M3.1** ✅ | Review-driven hardening: import-cycle, non-finite, noise floors, determinism | Correctness & honesty under scrutiny |
| **M4** ✅ | Blast-radius + external-asset lineage (dashboards/ML/APIs) | Business-level impact |
| **M5a** ✅ | Warehouse pushdown profiling (DuckDB + Snowflake dialect), detection-equivalence | Pushdown is detection-equivalent to local profiling |
| **M5b** ✅ | CLI (`dvi analyze`) + composite GitHub Action posting sticky PR reports | Real-user adoption path |
| **M6** ✅ | Editorial landing page + operator UI, static-exported to Vercel | Operator experience |

### Explicitly not built yet

- **Automatic BI/ML lineage discovery** (Tableau/Looker/feature stores) —
  downstream assets register via dbt exposures until then.
- **Warehouses beyond DuckDB** (executed in CI) and **Snowflake** (dialect +
  SQL-gen tests, not CI-executed) — another warehouse needs a new `SqlDialect`.
- **Multi-asset runs** — one `dvi analyze` run covers one asset.
- **Forges beyond GitHub** and **any autonomous remediation**.

## Contributing

Contributions are welcome — DVI is early and the [open issues](https://github.com/anuran-de/dvi/issues)
are the best place to start.

```bash
pip install -e ".[dev]"
pytest              # the full suite (also runs the demo + benchmark end to end)
ruff check .        # lint
```

A few conventions this project holds to:

- **Test-driven.** New behavior lands as a failing test first, then the code to
  pass it. CI runs the suite twice under different hash seeds as a determinism
  guard, so avoid depending on set/dict iteration order.
- **Deterministic and explainable.** Detection and ranking must not depend on an
  LLM or on unseeded randomness. Confidence numbers are either omitted or
  measured — never hand-tuned.
- **Few dependencies on purpose.** The engine uses only polars, duckdb,
  networkx, and pydantic. New runtime dependencies need a strong reason (this is
  why, e.g., Snowflake's `pyarrow`-pulling driver isn't in CI).

Open an issue to discuss anything larger before you build it.

## License

MIT — see [LICENSE](LICENSE).
