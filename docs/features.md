# DVI feature catalog + how-to-use

The single front-door catalog of everything DVI does, from the first detector to
the newest capability, each with a short **how to use it**. It is deliberately
skimmable: one section per feature, a *what it does* paragraph and a *how to use
it* block. For deeper reference, each section links to the focused doc rather
than duplicating it.

> **What DVI is.** DVI (Data Versioning Intelligence) is a self-hostable
> intelligence layer that catches **semantic** data incidents — the ones where
> the pipeline runs green (schema intact, row counts steady, freshness met, null
> checks passing) but a business number goes silently wrong. It sits *on top of*
> your existing stack (dbt, your warehouse, Git) and replaces none of it.

> **Reliability-first ethos.** Detection and root-cause ranking are
> **deterministic and explainable** — **no LLM sits in the decision path**. An
> LLM, if ever used, only *narrates* evidence; it never decides whether something
> changed. Confidence is either omitted (rank + evidence) or *calibrated and
> measured on held-out data*, never hand-tuned. DVI values reliability over
> breadth: every feature below is proven before it ships.

## Contents

- [The detector suite](#the-detector-suite)
  - [Value substitution](#value-substitution)
  - [Case / format normalization](#case--format-normalization)
  - [Category split / merge](#category-split--merge)
  - [Numeric distribution shift](#numeric-distribution-shift)
  - [Unit / scale shift](#unit--scale-shift)
- [Sources: file and warehouse](#sources-file-and-warehouse)
- [Lineage, change events, and root-cause analysis](#lineage-change-events-and-root-cause-analysis)
- [Blast radius and business impact](#blast-radius-and-business-impact)
- [The calibrated gate and severity model](#the-calibrated-gate-and-severity-model)
- [Calibrated confidence](#calibrated-confidence)
- [Reports: Markdown and JSON](#reports-markdown-and-json)
- [The CLI](#the-cli)
- [The GitHub Action](#the-github-action)
- [The incident store](#the-incident-store)
- [Multi-asset scanning](#multi-asset-scanning)
- [Real-data evaluation harness](#real-data-evaluation-harness)
- [The web UI](#the-web-ui)

---

## The detector suite

DVI's core is a set of five deterministic **signatures**, each a test over two
column profiles (a before and an after distribution). More specific signatures
suppress more general ones on the same column, so a rigid `×100` re-encoding
reports as a *unit/scale shift*, not a generic *distribution shift*. Cheaper
commodity checks (null-explosion, cardinality, volume, duplicate-rate, schema/
type) are slotted in where they are inexpensive.

You rarely call a detector directly — the CLI runs all of them over each asset —
but each is a public function in `dvi.detection`. Column selection (which columns
to profile) is controlled by `columns` in `dvi.toml`; omitting it profiles all
shared columns.

### Value substitution

**What it detects.** A category renamed or replaced in place, with mass
conserved — one value's share collapses while another absorbs it.
`detect_value_substitution` (signature #1).

*Example incident:* `country = "UK"` becomes `country = "United Kingdom"`;
downstream logic filtering on `"UK"` silently under-counts a region.

**How to use it.** Run `dvi analyze` over the before/after snapshot; the
signature fires automatically. One-to-many / many-to-one shapes are deferred to
category split/merge so a rename is never mislabelled.

### Case / format normalization

**What it detects.** The same categories re-spelled — casefolding or whitespace
changes — with the normalized set and per-category masses preserved.
`detect_case_format_normalization` (signature #2).

*Example incident:* `status = "active"` becomes `status = "ACTIVE"`, or a stray
leading space appears; a downstream `WHERE status = 'active'` now matches
nothing.

**How to use it.** Automatic during `dvi analyze`. It is robust to noise-sized
tail categories (significant-set comparison plus a minimum-share gate on the
re-spellings).

### Category split / merge

**What it detects.** One category fanning out into many, or many collapsing into
one, with total mass conserved. `detect_category_split_merge` (signature #3).

*Example incident:* a single `segment = "SMB"` splits into `"Small"` and
`"Medium"`, redistributing a customer count that a dashboard groups on.

**How to use it.** Automatic during `dvi analyze`. Share moves must clear a
two-proportion sampling-noise floor, so the same redistribution reads as noise at
small `n` and as signal at large `n`.

### Numeric distribution shift

**What it detects.** A behavioral change in a numeric column's shape or location,
measured as normalized quantile movement against a tunable threshold.
`detect_numeric_distribution_shift` (signature #4; default threshold
`DEFAULT_DISTRIBUTION_THRESHOLD`).

*Example incident:* a `revenue` column's median jumps because an upstream join
started double-counting.

**How to use it.** Automatic during `dvi analyze`. Numeric profiling keeps only
finite values (dropping `NaN`/`inf`) and applies a sample-size noise floor so two
halves of the same population do not trip it at small `n`.

### Unit / scale shift

**What it detects.** A rigid affine re-encoding of a numeric column — a
multiplicative rescale (dollars → cents) or an additive shift (a timezone
offset) — fitted from robust quantile anchors. `detect_unit_scale_shift`
(signature #5).

*Example incident:* an `amount` column is silently multiplied by 100 when a
source switches from dollars to cents; totals inflate 100×.

**How to use it.** Automatic during `dvi analyze`. A no-op rescale reads as
factor ≈ 1 and a no-op shift as offset ≈ 0, so near-constant columns far from
zero do not false-fire; any real large move there is still caught by #4.

---

## Sources: file and warehouse

**What it does.** DVI reads each asset's before/after snapshot from one of two
source kinds, selected by `kind` in `[source]` (or `[assets.source]`):

- **file** — `.parquet`, `.csv`, or `.ndjson`, read natively by polars.
- **warehouse** — a DuckDB database file opened read-only, with profiling
  **pushed down into SQL** so a billion-row table moves a handful of aggregates,
  not the table. Snowflake uses the same seam via `SnowflakeDialect` (SQL-gen
  tested, not executed in CI). Table identifiers are validated and quoted per
  dotted part, closing the SQL-injection surface.

**How to use it.**

```toml
# file source
[source]
kind = "file"
before = "artifacts/fct_orders.main.parquet"
after  = "artifacts/fct_orders.pr.parquet"

# --- or warehouse source ---
[source]
kind = "warehouse"
database = "warehouse.duckdb"
before_table = "prod.fct_orders"
after_table  = "pr.fct_orders"
```

See [docs/warehouse-pushdown.md](warehouse-pushdown.md) for the executor
contract and the detection-equivalence guarantee between the pushdown and local
paths.

---

## Lineage, change events, and root-cause analysis

**What it does.** A detected change is only a *symptom* until DVI corroborates it
against **when** a deploy happened and **whether** it propagates downstream. DVI
loads your dbt `manifest.json` into a lineage graph (models + exposures, backed
by networkx), attributes symptoms to **change events**, and ranks likely root
causes with evidence — an isolated blip stays a symptom; a change that correlates
with a deploy and propagates downstream becomes an *incident*.

Change events come from two unioned, de-duplicated sources:

- **Declared** `[[changes]]` (or `[[assets.changes]]`) — explicit events with an
  `id`, `targets` (nodes in the manifest), and a required ISO-8601 `timestamp`.
- **Auto-derived** — in CI, DVI derives candidate events from the commits in the
  PR range and maps changed dbt model files to the assets they touch, so
  `[[changes]]` is optional. The range resolves from `[git] base`/`head` →
  `$GITHUB_BASE_REF`/`$GITHUB_SHA` → default `HEAD~1..HEAD`.

If neither a declared nor a derived change exists, the run errors (exit `2`) —
DVI never attributes an incident to nothing.

**How to use it.**

```toml
[lineage]
manifest = "target/manifest.json"

[[changes]]                     # optional; unioned with git-derived events
id = "pr-1234"
label = "Refactor revenue rollup"
targets = ["model.shop.stg_orders"]
timestamp = 2026-08-30T12:00:00Z

[git]                           # optional; controls the auto-derived range
base = "main"
head = "HEAD"
```

Deriving from history needs a full checkout (`actions/checkout` with
`fetch-depth: 0`). See [docs/architecture.md](architecture.md) for the module
map and [docs/cli.md](cli.md) for the full config reference.

---

## Blast radius and business impact

**What it does.** An incident's blast radius extends past data assets to the
business consumers downstream of them. DVI parses dbt **exposures** (dashboards,
ML features, applications, notebooks, analyses) as typed lineage nodes, projects
an incident's affected assets onto the exposures they reach, and names the impact
by consumer type and count. A business-critical consumer can **escalate**
severity into the `critical` tier — but only for a *material* change, so an
immaterial flicker under a critical dashboard stays low.

**How to use it.** Register downstream consumers as dbt exposures in your project
(optionally with `meta.criticality`); DVI reads them from the same
`manifest.json` as your models. The report's "Affected downstream assets" and
business-impact block are rendered automatically when exposures are reached.

---

## The calibrated gate and severity model

**What it does.** DVI maps each incident to a severity on the ladder
`low < medium < high < critical` and compares it to a configurable threshold. The
`[gate]` decides whether a run *blocks* — it drives the process exit code that CI
reads.

- `fail_on` — the minimum severity that trips the gate (default `high`).
- `model` — attach calibrated confidence to symptoms (default `true`).

**How to use it.**

```toml
[gate]
fail_on = "high"     # low | medium | high | critical
model = true         # attach calibrated confidence
```

Exit codes: `0` (no incident, or below `fail_on`), `1` (gate tripped), `2` (could
not run). For multi-asset runs one gate reads the worst severity across assets —
see [Multi-asset scanning](#multi-asset-scanning).

---

## Calibrated confidence

**What it does.** When the gate's `model` is on, each fired symptom carries a
*measured* probability that it is a real change — not a hand-tuned number. The
model is a small pure-Python logistic regression (no numpy/sklearn) over three
features: `magnitude`, `significance_margin`, and `log10` of the sample size. Its
reliability is proven **out-of-fold**: on the fired-symptom set, out-of-fold
ECE ≈ 0.05. The shipped model is refit on all data and frozen to
`coefficients.json`, so inference needs no training data.

**How to use it.** Leave `model = true` in `[gate]` (the default). The confidence
surfaces as a "Confidence: 70%" line in the report. See
[docs/validation.md](validation.md) for the reliability methodology and numbers.

---

## Reports: Markdown and JSON

**What it does.** Every run renders the same result two ways so the human and
machine views never disagree:

- **Markdown** (`dvi-report.md`) — a PR-ready comment. The `<!-- dvi-report -->`
  marker is on the first line so the GitHub Action can find and update one sticky
  comment.
- **JSON** (`dvi-report.json`) — the machine envelope: `asset`, `severity`,
  `incident` (title, summary, evidence, affected assets, business impact),
  `gate: {fail_on, failed}`, and `generated_at`.

**How to use it.** They are written to `--output-dir` on every `dvi analyze` run;
the Markdown is also echoed to stdout. The multi-asset report shapes are
documented in [Multi-asset scanning](#multi-asset-scanning) and
[docs/cli.md](cli.md).

---

## The CLI

**What it does.** `dvi analyze` is the single entrypoint: load and validate
`dvi.toml`, analyze the snapshot(s), render the reports, and return the gate exit
code.

**How to use it.**

```bash
pip install dvi
dvi analyze --config dvi.toml --output-dir .dvi
```

CI can inject PR-specific file paths without rewriting the config using
`--source-before` / `--source-after` (single-asset file configs only). Full
reference: [docs/cli.md](cli.md).

---

## The GitHub Action

**What it does.** A composite Action runs `dvi analyze` on a pull request, posts
the report as a **sticky** comment (updated in place, keyed off the hidden
`<!-- dvi-report -->` marker) via the runner's `gh` CLI, and fails the check on
the CLI's exit code. No third-party action is required.

**How to use it.**

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

A ready-to-copy workflow ships at `.github/workflows/dvi-example.yml`. The
`fetch-depth: 0` full clone is required so DVI can derive change events from the
PR range.

---

## The incident store

**What it does.** Detection is stateless by default. An optional, opt-in local
store gives incidents a stable identity so recurrences dedupe and an asset's
history is queryable over time. It is a dependency-free local SQLite file; the
incident identity is `SHA-256(asset ⊕ primary_signature ⊕ change_event_id)`, and
the run timestamp is the incident's detection time (anchored to declared change
timestamps, not the wall clock), so re-running the same snapshot upserts onto one
row instead of duplicating.

**How to use it.**

```toml
[store]
path = ".dvi/incidents.db"     # created on first run; omit to stay stateless
```

Recording never changes the exit code. Query API: `history(asset)`,
`get(identity_key)`, `prune(before=…)`. See
[docs/incident-store.md](incident-store.md).

---

## Multi-asset scanning

**What it does.** One `dvi.toml` can declare a list of `[[assets]]` instead of a
single top-level asset, so a single run scans several assets and emits **one
aggregated report** guarded by **one worst-severity gate**. Assets are analyzed
in deterministic **sorted-by-name** order. The two modes (legacy single-asset vs.
`[[assets]]`) are mutually exclusive; the legacy config is byte-identical to
before.

The single exit code is the worst outcome across assets, with precedence
**gate trip (`1`) > errored asset (`2`) > clean (`0`)**. The aggregated Markdown
keeps the marker on line 1, then a summary table, the shared gate line, and a
per-asset drill-down (an asset that could not run shows `⚠️ ERRORED` rather than
aborting the run). The aggregated JSON is
`{assets: [...], gate: {fail_on, failed, worst_severity}, generated_at}`, where
each asset entry adds a per-asset `error` field.

**How to use it.**

```toml
[lineage]
manifest = "target/manifest.json"

[gate]
fail_on = "high"

[[assets]]
name = "model.shop.fct_orders"
columns = ["country"]
[assets.source]
kind = "file"
before = "before/fct_orders.parquet"
after = "after/fct_orders.parquet"
[[assets.changes]]
id = "pr-42"
targets = ["model.shop.stg_orders"]
timestamp = 2026-09-10T09:00:00

[[assets]]
name = "model.shop.dim_customer"
columns = ["segment"]
[assets.source]
kind = "file"
before = "before/dim_customer.parquet"
after = "after/dim_customer.parquet"
```

Shared sections (`[lineage]`, `[git]`, `[gate]`, `[store]`) stay top-level and
apply to every asset; per-asset `name`/`source`/`columns`/`changes` live inside
each `[[assets]]` entry. Full schema and report shapes: [docs/cli.md](cli.md).

---

## Real-data evaluation harness

**What it does.** A synthetic benchmark can flatter its own detector, so DVI is
validated against real public datasets (diamonds, adult census, online retail).
The harness measures two things: **specificity** on real-vs-real splits (disjoint
samples of the same distribution, where any fired symptom is by construction a
false positive) and **injected recall** (one known change per detector family
planted into a real sample). It reports pooled recall and pooled false-positive
rate, plus calibration (ECE/MCE/Brier) on real data.

*Committed, CI-reproducible result:* pooled recall 96.0%, pooled false-positive
rate 1.9%.

**How to use it.**

```bash
python -m dvi.benchmark.real_eval
```

The datasets are bundled so it runs offline in CI. Full methodology, per-dataset
numbers, and the git-ignored NYC-taxi scale run: [docs/validation.md](validation.md).

There is also a synthetic scenario suite (`python scripts/benchmark.py`) that
reaches 100% recall at 0% false positives on labelled positives, negatives, and
benign decoys, and scores root-cause top-1 accuracy under distractor deploys.

---

## The web UI

**What it does.** An editorial landing page plus an operator UI (incident
dashboard, incident detail with timeline and evidence, and a blast-radius graph),
built with Next.js + Tailwind + Framer Motion and statically exported. It renders
from real pipeline output, not mock data.

**How to use it.** The live demo is deployed at
[dvintelligence.vercel.app](https://dvintelligence.vercel.app). To run or deploy
it yourself, and to regenerate fixtures from real runs, see
[docs/frontend.md](frontend.md).
