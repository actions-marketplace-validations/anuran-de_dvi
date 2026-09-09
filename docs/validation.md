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
| nyc_taxi | urban mobility (scale) | ≈39.7M full; 200k reservoir sample for experiments (local only) | negative fares, zero-distance trips, garbage timestamps |

> The committed parquet files under `data/` are the source of truth for these
> numbers, not `scripts/prep_real_datasets.py`. A future polars release could
> change the written byte layout, but the committed files — and the seed-stable
> row content the harness reads by column — do not.

## Results (committed datasets, CI-reproducible)

Positives are **injected** into **real distributions** (real data underneath,
ground-truth labels on top) — this is injected-label evaluation, not observed
production incidents. Real-vs-real measures specificity on unchanged real data.

| dataset | domain | recall | false-positive rate |
|---------|--------|--------|---------------------|
| adult | census income | 1.000 | 0.006 |
| diamonds | retail pricing | 0.887 | 0.000 |
| online_retail | e-commerce transactions | 0.993 | 0.089 |
| **pooled** | — | **0.960** | **0.019** |

The diamonds row is deliberately reported as-is: 0.887 reflects a real
detector-precedence limitation on the diamonds `cut` case-format recipe (a
higher-precedence signature claims the column before the case-normalization
signature gets a look), not a tuning artifact. It is not explained away here.

## Calibration on real data

Reliability (n=912, positives=432)

| bin | n | predicted | empirical | gap |
|-----|---|-----------|-----------|-----|
| 0.0-0.1 | 471 | 0.000 | 0.000 | 0.000 |
| 0.8-0.9 | 9 | 0.869 | 0.000 | 0.869 |
| 0.9-1.0 | 432 | 0.996 | 1.000 | 0.004 |

ECE = 0.0107   MCE = 0.8694   Brier = 0.0075

(ECE is count-weighted, dominated by the extremes; MCE is the worst bin. 0 of
912 predictions land in [0.2, 0.8].)

The confidence model is **not re-fit** here — these pairs test whether the
shipped confidence (fit on the synthetic set) stays honest on real data.

## Scale (NYC taxi, developer-run)

The NYC taxi trip-record dataset is git-ignored (776 MB, 39,717,684 rows) and
not part of CI. It is used to check that DVI's profiling scales to production
volumes, not to add a fourth row to the committed pooled result above.

Experiments run over a deterministic 200,000-row DuckDB `REPEATABLE` reservoir
sample of the full parquet (CLI default `n=1000`, `trials=30`, `seed=0`);
throughput is measured separately at full scale.

| dataset | recall | false-positive rate |
|---------|--------|---------------------|
| nyc_taxi | 0.800 | 0.008 |

- Full parquet: 39,717,684 rows (776 MB, git-ignored).
- Throughput: 135,280,842 rows/s (DuckDB full-parquet scan; avg `fare_amount` ≈ $19.81).

Reproduce: `python scripts/prep_nyc_taxi.py` then
`python -m dvi.benchmark.real_eval --include-local`. The `--include-local` flag
adds any present git-ignored local datasets; without it (the default) — and on a
clean checkout — the CLI prints only the 3 committed datasets, so the committed
pooled numbers above are byte-identical whether or not taxi has been prepped.

## Reproduce the committed-dataset numbers

    python -m dvi.benchmark.real_eval
