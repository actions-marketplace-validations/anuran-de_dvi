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
