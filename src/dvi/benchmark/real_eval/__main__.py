"""``python -m dvi.benchmark.real_eval`` — print the real-data evaluation report.

By default this prints the committed, CI-reproducible report over the three
committed datasets. Pass ``--include-local`` to additionally run any
git-ignored local datasets (e.g. the NYC taxi scale sample) that are present on
the machine — kept off by default so the default report is byte-identical
whether or not a developer has prepped local data.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence

from .runner import render_report, run_registry


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m dvi.benchmark.real_eval")
    parser.add_argument(
        "--include-local",
        action="store_true",
        help="also run git-ignored local datasets (e.g. NYC taxi) when present; "
        "off by default so the report stays CI-reproducible",
    )
    return parser.parse_args(argv)


def build_report(
    argv: Sequence[str] | None = None, *, n: int = 1000, trials: int = 30, seed: int = 0
) -> str:
    """Render the evaluation report; committed-only unless ``--include-local``."""
    args = _parse_args(argv)
    report = run_registry(
        n=n, trials=trials, seed=seed, committed_only=not args.include_local
    )
    return render_report(report)


if __name__ == "__main__":
    print(build_report())
