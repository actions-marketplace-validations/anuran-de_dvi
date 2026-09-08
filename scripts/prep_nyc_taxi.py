"""Prepare the git-ignored NYC-taxi parquet for the scale dimension of the harness.

DuckDB streams the raw 4.3 GB CSV (``nyc_taxi_data.csv`` at the repo root),
filters to plausible bounds (the raw file has ~731k negative fares, ~776k
zero-distance trips, and timestamps spanning 2002-2026), and writes a
deterministic parquet to ``data/local/nyc_taxi.parquet`` -- both raw and output
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
    # DuckDB's COPY ... TO does not support a parameterized destination (the
    # placeholder is resolved against the wrong clause); both paths are
    # produced by this script, not user input, so literal interpolation with
    # single-quote escaping is safe.
    raw_path = str(RAW).replace("'", "''")
    out_path = str(OUT).replace("'", "''")
    con.execute(
        f"""
        COPY (
            SELECT
                VendorID, tpep_pickup_datetime, tpep_dropoff_datetime,
                passenger_count, trip_distance, PULocationID, DOLocationID,
                payment_type, fare_amount, tip_amount, total_amount
            FROM read_csv_auto('{raw_path}')
            WHERE fare_amount >= 0
              AND trip_distance > 0
              AND tpep_pickup_datetime >= TIMESTAMP '2010-01-01'
              AND tpep_pickup_datetime <  TIMESTAMP '2025-01-01'
        ) TO '{out_path}' (FORMAT parquet)
        """
    )
    rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(OUT)]).fetchone()[0]
    print(f"wrote {OUT} ({rows} rows)")


if __name__ == "__main__":
    main()
