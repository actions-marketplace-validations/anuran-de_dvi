# Multi-asset scanning — one config, many assets (#12)

**Status:** approved for implementation
**Issue:** [#12](https://github.com/anuran-de/dvi/issues/12)
**Date:** 2026-09-10

## Problem

Today a `dvi.toml` describes exactly one asset: a top-level `asset` name and a
single `source` (file pair or warehouse table pair). A repository that governs
many datasets must therefore keep one config per asset and invoke the CLI /
Action once per asset — N configs, N reports, N sticky comments, N gate exit
codes to reconcile. There is no single answer to "did anything regress in this
PR across all my assets?".

## Goal

One `dvi.toml` analyzes many assets. The run produces a single aggregated
report (Markdown + JSON), a single worst-severity gate decision, and a single
exit code, with assets processed in a deterministic order.

**Non-goals (YAGNI, explicitly deferred):**

- **Asset auto-discovery** — assets are declared explicitly; the tool does not
  infer them from the lineage manifest. (Clarifying decision: "Explicit list
  only".)
- **Parallel execution** — assets are analyzed sequentially; polars keeps its
  own intra-asset threads. A threadpool over assets is deferred until a real
  run proves it necessary. (Clarifying decision: "Sequential first".)
- Per-asset gate overrides, per-asset store routing — the gate, git, lineage
  and store config remain shared at the top level.

## Chosen approach

Approach A — **one `analyze` command, config auto-detects single vs. multi,
normalize to a canonical list internally.**

- `DviConfig` accepts *either* today's top-level `asset` + `source` (legacy)
  *or* a new `[[assets]]` list — never both. A validator normalizes both forms
  into a canonical `list[AssetSpec]`, so the analysis loop never sees the
  legacy/multi split.
- `incident_from_config` is refactored to extract a per-asset
  `analyze_one(spec, shared) -> AssetResult`; today's logic becomes its body.
- `main` builds shared context once, loops over the sorted specs, collects
  `AssetResult`s, and renders/gates the aggregate.
- **Back-compat guarantee:** a legacy single-asset config renders
  byte-identical Markdown + JSON to today, with the same exit codes and the
  same `<!-- dvi-report -->` sticky-comment marker. The aggregated
  summary-table-plus-drill-down shape appears *only* when the config declares
  `[[assets]]`.

Rejected alternatives: (B) a separate `analyze-all` subcommand — duplicates
the command surface and splits docs/Action wiring for no benefit; (C) a
wrapper script that shells the single-asset CLI N times — no shared context,
N sticky comments, no single gate, defeats the issue.

## Components

Each unit has one purpose, a well-defined interface, and is testable in
isolation.

### 1. Config schema + normalization (`src/dvi/cli/config.py`)

New model, `extra="forbid"` like the existing models:

```
class AssetConfig(BaseModel):
    name: str
    source: FileSource | WarehouseSource   # discriminated on "kind"
    changes: list[ChangeConfig] = []
    columns: list[str] | None = None
```

`DviConfig` becomes additive — legacy fields optional, new `assets` list added,
shared config unchanged:

```
class DviConfig(BaseModel):
    # legacy single-asset (all optional now)
    asset: str | None = None
    source: FileSource | WarehouseSource | None = None
    changes: list[ChangeConfig] = []
    columns: list[str] | None = None
    # new multi-asset
    assets: list[AssetConfig] = []
    # shared across all assets (unchanged)
    lineage: LineageConfig
    git: GitConfig = GitConfig()
    gate: GateConfig = GateConfig()
    store: StoreConfig | None = None
```

**Validation** (model validator; every failure surfaces as `DviError` via
`load_config`):

- **Exactly one mode.** Legacy is "active" when `asset` and `source` are both
  present; multi is "active" when `assets` is non-empty. Both active →
  `DviError`. Neither active → `DviError`.
- **Multi mode forbids top-level asset fields.** When `assets` is non-empty,
  any of top-level `asset` / `source` / `changes` / `columns` being set →
  `DviError` with a message directing the user to declare them per asset. This
  keeps changes unambiguously per-asset.
- **Unique names.** Duplicate `name` across `assets` → `DviError`.

**Canonical shape + normalizer:**

```
@dataclass(frozen=True)
class AssetSpec:
    name: str
    source: FileSource | WarehouseSource
    changes: list[ChangeConfig]
    columns: list[str] | None

def normalized_assets(config: DviConfig) -> list[AssetSpec]:
    # legacy -> [one AssetSpec built from top-level fields]
    # multi  -> [AssetSpec per AssetConfig], declaration order preserved here
```

Whether the config carries `[[assets]]` (vs. legacy) is recorded so `main` can
choose the byte-identical single-asset render path — e.g. a
`config.is_multi_asset` property (`len(config.assets) > 0`).

### 2. Per-asset analysis (`src/dvi/cli/sources.py`)

**Shared context, built once** before the loop so the manifest is never
re-read and git is never re-run per asset:

```
@dataclass(frozen=True)
class SharedContext:
    lineage: Lineage                 # loaded manifest
    nodes: set[str]                  # lineage node ids (change-target validation)
    derived_changes: list[ChangeEvent]   # git-derived, once for the whole run
    model: <calibrated model>        # loaded once
    gate: GateConfig
    store: StoreConfig | None
```

**Result value** — one asset's outcome, failure captured as a value:

```
@dataclass(frozen=True)
class AssetResult:
    name: str
    incident: Incident | None   # analysed cleanly; None = no incident found
    error: str | None           # could-not-run reason; incident is None when set
```

Invariant: `error is not None` ⇒ ERRORED (incident ignored); otherwise
`incident` is the analysis result (possibly `None` for "no incident").

**Extraction** — `analyze_one(spec: AssetSpec, shared: SharedContext) ->
AssetResult`:

- Resolve this asset's changes: `resolved = dedup(spec.changes ∪
  shared.derived_changes)` using the existing `_dedup_key`; validate change
  targets against `shared.nodes` (today's behavior).
- Guard: `resolved` empty → `AssetResult(spec.name, None, "no change events
  for asset")`. This is today's deliberate could-not-run guard, scoped to one
  asset instead of aborting the whole run.
- `observed_at = max(c.timestamp for c in resolved)`.
- File source → `analyze_change(spec.name, before, after, observed_at,
  shared.lineage, resolved, spec.columns, shared.model)`.
  Warehouse source → `SqlProfileSource(...).profile(spec.columns)` →
  `analyze_change_from_profiles(...)`.
- Return `AssetResult(spec.name, incident, None)`.
- **Scoped error capture:** only deliberate could-not-run failures are turned
  into `AssetResult.error` — `DviError`, and the `OSError` / DuckDB
  load-or-query failures for *this asset's* source. Programming errors are not
  caught; they crash loudly so one bad table cannot mask a real bug. No blanket
  `except Exception`.

`incident_from_config(config) -> Incident | None` is retained as a thin
back-compat wrapper: normalize to the single legacy spec, build `SharedContext`,
call `analyze_one`, and re-raise `AssetResult.error` as `DviError` so existing
callers/tests keep today's exception contract.

### 3. Rendering (`src/dvi/cli/render.py`)

**Single-asset path is frozen.** When `not config.is_multi_asset` (legacy
config, one asset), `main` calls today's `render_markdown` / `render_json`
unchanged. Output — including the `<!-- dvi-report -->` marker — is
byte-identical to today. Pinned by a golden test (§Testing).

**Aggregated path** (whenever `[[assets]]` is declared, even a single entry):

`render_multi_markdown(results: list[AssetResult], *, fail_on, gate_failed) ->
str`:

- Line 1 is the `<!-- dvi-report -->` marker (unchanged constant) so the
  Action's sticky-comment update still finds and replaces the comment.
- **Summary table**, assets in sorted order:
  `| Asset | Result |` where Result is `<emoji> <severity>` for an incident,
  `✅ clean` for `incident is None and error is None`, or
  `⚠️ ERRORED — <message>` for an errored asset. Emoji reuse the existing
  `_EMOJI` map.
- **Drill-down**, one block per asset under an `## <name>` heading: for an
  incident, today's single-asset Markdown body reused verbatim; for clean, a
  one-line "no incident"; for errored, the message.

`render_multi_json(results, *, fail_on, gate_failed, generated_at) -> str`:

```
{
  "assets": [
    {"asset": name, "severity": level|null, "incident": {...}|null, "error": msg|null},
    ...                                   # sorted by asset name
  ],
  "gate": {"fail_on": fail_on, "failed": gate_failed, "worst_severity": level|null},
  "generated_at": generated_at
}
```

Each `incident` element reuses today's single-asset JSON incident shape, so a
downstream parser scales by iterating `assets`.

### 4. Gate + exit codes + ordering (`src/dvi/cli/main.py`, `gate.py`)

- **Ordering:** `main` sorts specs once by `name`
  (`sorted(specs, key=lambda s: s.name)`, case-sensitive). Analysis, summary
  table, drill-down and the JSON `assets` array all preserve this order —
  deterministic regardless of declaration order.
- **Worst-severity gate:** across all non-errored assets, take the max severity
  by `SEVERITY_LEVELS` rank → `worst`; `gate_failed = gate_failed(worst,
  fail_on)`. `worst_severity` is `null` when every asset is clean or errored.
- **Exit-code precedence** (approved), computed after the loop:
  1. any asset trips the gate → **1**
  2. else any asset errored → **2**
  3. else → **0**

  gate-trip outranks errored: a real detected incident is the loudest signal.
  Legacy single-asset reproduces today's exact codes as a natural special case,
  including the errored-single-asset exit-2 + stderr path.

- **Store:** each non-errored asset's incident is recorded via the existing
  `_record_incident` against the shared store, in sorted order.

## Data flow

```
load_config(dvi.toml)                      # pydantic + validation -> DviConfig
  -> normalized_assets(config)             # -> list[AssetSpec] (legacy=1, multi=N)
  -> build SharedContext once              # lineage manifest, git derivation, model
  -> specs sorted by name
  -> for spec in specs: analyze_one(spec, shared) -> AssetResult   # sequential
  -> worst-severity gate over results
  -> if config.is_multi_asset:
         render_multi_markdown / render_multi_json
     else:
         render_markdown / render_json     # byte-identical legacy path
  -> write dvi-report.md / dvi-report.json
  -> record each incident to store
  -> exit code by precedence (1 > 2 > 0)
```

## Error handling

- Config errors (both/neither mode, forbidden top-level fields, duplicate
  names) → `DviError` from `load_config` → exit **2**, message on stderr. The
  whole run cannot start with a malformed config.
- Per-asset could-not-run (no change events, missing file, unreadable table) →
  captured as `AssetResult.error`; the run continues, the asset is shown
  ERRORED, and exit **2** results only if no other asset trips the gate.
- Programming errors are never swallowed — no blanket `except Exception` in the
  loop.
- Legacy single-asset error path is unchanged: one errored asset → today's
  exit-2 + stderr, not an aggregated report.

## Testing (strict TDD, RED first)

**Config** (`tests/test_cli_config.py`):
- legacy single-asset config loads and normalizes to one `AssetSpec`;
- `[[assets]]` list normalizes to N specs in declaration order;
- both modes set → `DviError`; neither set → `DviError`;
- top-level `changes`/`source` alongside `[[assets]]` → `DviError`;
- duplicate asset `name` → `DviError`.

**Analysis** (`tests/test_cli_sources.py`):
- `analyze_one` returns `AssetResult(incident=…)` for a file-source incident,
  and `(incident=None, error=None)` for a clean pair;
- zero resolved changes → `AssetResult(error="no change events…")`;
- a bad source (missing file / unreadable table) → `error` set, not raised;
- `SharedContext` is built once — assert the manifest loader is called a single
  time across a two-asset run;
- `incident_from_config` back-compat wrapper still raises `DviError` on the
  legacy no-change case.

**Render / gate / exit** (`tests/test_cli_render.py`, `tests/test_cli_main.py`):
- **golden back-compat:** a legacy single-asset run's `dvi-report.md` / `.json`
  are byte-identical to the current committed fixtures;
- multi-asset Markdown: marker on line 1, one summary row per asset in sorted
  order, an errored asset shown as `⚠️ ERRORED`;
- multi-asset JSON: `assets` array sorted, `gate.worst_severity` present;
- exit precedence end-to-end: trip + errored → **1**; clean + errored → **2**;
  all clean → **0**.

## Documentation (updated with this issue)

- `README.md` — multi-asset section with a copy-paste `[[assets]]` `dvi.toml`.
- `docs/cli.md` — config schema (legacy vs. `assets`), aggregated report shape,
  exit-code precedence.
- `action.yml` — note the sticky comment now handles aggregated reports.
- `CHANGELOG.md` — "Added: multi-asset scanning".
- **`docs/features.md` (the grail):** a new comprehensive **Feature &
  How-To-Use** catalog covering every DVI capability from scratch to now — the
  five detectors (value substitution, case/format normalization,
  category split/merge, numeric distribution shift, unit/scale shift), file &
  warehouse sources, lineage + root-cause analysis, the calibrated gate, the
  GitHub Action, the incident store, and the real-data evaluation harness —
  each with a "what it detects / how to use it" block, with multi-asset added
  as the newest entry. This is the canonical marketing/publishing artifact and
  is updated on every future feature. First cut lands with this issue.
