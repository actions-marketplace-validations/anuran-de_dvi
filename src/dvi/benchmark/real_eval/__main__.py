"""``python -m dvi.benchmark.real_eval`` — print the real-data evaluation report."""
from __future__ import annotations

from .runner import render_report, run_registry

if __name__ == "__main__":
    print(render_report(run_registry()))
